"""Пул заранее сгенерированных стандартных экземпляров для первого задания сессии."""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, update

from taskgen.app import create_app
from taskgen.config import GenerationSettings
from taskgen.db import PoolInstance
from taskgen.generation import pool
from tests.bank.conftest import FakeEmbedder
from tests.evaluation.fakes import FakeLLM
from tests.generation.fakes import LocalSandbox
from tests.generation.world import ADMIN, ModelStub, World

pytestmark = pytest.mark.integration

ONE_TASK = {"selection_override": {"tasks_per_test": 1}}


@pytest.fixture
def settings(settings):
    generation = GenerationSettings(autostart=False, pool_size=2, pool_max_age_hours=1)
    return settings.model_copy(update={"generation": generation})


def refill(world, *names: str) -> int:
    """Пополнить пул шаблонов этой области; имена — суффиксы, пусто — все шаблоны области."""
    templates = world.templates()
    ids = [templates[n].id for n in names] if names else [t.id for t in templates.values()]
    return world._run(world.state.generation_worker.refill_pool, ids)


def llm_calls(world) -> int:
    return len(world.state.llm.calls)


def test_refill_keeps_pool_size_of_checked_standard_instances(world):
    world.add_templates(world.code(), world.essay())
    assert refill(world) == 4
    instances = world.pool()
    assert len(instances) == 4
    by_kind = {kind: [i for i in instances if i.kind == kind] for kind in ("code", "essay")}
    assert len(by_kind["code"]) == len(by_kind["essay"]) == 2
    assert all(i.validated for i in by_kind["code"])
    assert all(i.public_payload["environment"] for i in by_kind["code"])
    # Повторное пополнение ничего не добавляет: пул уже полон.
    assert refill(world) == 0
    assert len(world.pool()) == 4


def test_pool_instance_is_issued_to_one_candidate_and_removed(world):
    world.add_templates(world.code())
    refill(world)
    stock = world.pool()
    assert len(stock) == 2
    calls = llm_calls(world)

    first = world.create(profile={"resume": "Резюме, которое пул не видит"}, **ONE_TASK)
    assert first.status_code == 201, first.text
    [task] = world.tasks(first.json()["id"])
    # Первое задание взято из пула целиком, без обращения к модели и без профиля.
    issued = next(i for i in stock if i.seed == task.seed)
    assert task.status == "ready"
    assert task.statement == issued.statement
    assert task.hidden_payload == issued.hidden_payload
    assert task.personalization is None
    assert llm_calls(world) == calls
    assert [i.id for i in world.pool()] == [i.id for i in stock if i.id != issued.id]

    world.create(**ONE_TASK)
    world.create(**ONE_TASK)
    world.wait()
    seeds = [world.tasks(s.id)[0].seed for s in world.sessions()]
    # Каждый экземпляр пула выдан ровно одному кандидату; третий генерировался на месте.
    assert len(set(seeds)) == 3
    assert {i.seed for i in stock} <= set(seeds)
    assert world.pool() == []
    assert llm_calls(world) == calls + 1


def test_concurrent_takes_get_one_instance_once(world):
    world.add_templates(world.code())
    template = world.templates()["code"]
    refill(world)
    [keep, *_] = world.pool()

    async def take_both():
        sessionmaker = world.state.sessionmaker
        async with sessionmaker() as db, db.begin():
            await db.execute(
                PoolInstance.__table__.delete()
                .where(PoolInstance.id != keep.id)
                .where(PoolInstance.template_id == template.id)
            )

        async def take():
            async with sessionmaker() as db, db.begin():
                return await pool.take(db, template.id, max_age=timedelta(hours=1))

        return await asyncio.gather(take(), take())

    results = world._run(take_both)
    taken = [r for r in results if r is not None]
    assert len(taken) == 1 and taken[0]["id"] == keep.id
    assert world.pool() == []


def test_instance_older_than_max_age_is_not_issued_and_is_replaced(world):
    world.add_templates(world.code())
    refill(world)
    stale = world.pool()

    async def age():
        async with world.state.sessionmaker() as db, db.begin():
            await db.execute(
                update(PoolInstance)
                .where(PoolInstance.id.in_([i.id for i in stale]))
                .values(created_at=func.now() - timedelta(hours=2))
            )

    world._run(age)
    session = world.create(**ONE_TASK).json()
    [task] = world.tasks(session["id"])
    assert task.status == "ready"
    assert task.seed not in {i.seed for i in stale}
    assert {i.id for i in world.pool()} == {i.id for i in stale}

    assert refill(world) == 2
    fresh = world.pool()
    assert len(fresh) == 2 and not {i.id for i in fresh} & {i.id for i in stale}


def test_explicit_seed_bypasses_pool_for_reproducibility(world):
    world.add_templates(world.code())
    refill(world)
    stock = world.pool()
    session = world.create(seed=5, **ONE_TASK).json()
    [task] = world.tasks(session["id"])
    assert task.seed not in {i.seed for i in stock}
    assert len(world.pool()) == 2


def test_new_version_of_template_drops_old_instances(world):
    world.add_templates(world.code())
    refill(world)
    old = world.pool()
    world.add_templates(world.code(description="Новая версия описания"))
    refill(world)
    fresh = world.pool()
    assert len(fresh) == 2
    assert not {i.id for i in fresh} & {i.id for i in old}
    current = world.templates()["code"]
    assert {i.template_id for i in fresh} == {current.id}


def test_upload_through_api_refills_pool(world):
    # Загрузка через API ещё и считает эмбеддинг описания; подделке LLM это не под силу.
    world.state.embedder = FakeEmbedder({})
    body = world.code("api")
    response = world.client.post(
        "/templates",
        content=body.encode(),
        headers={**ADMIN, "Content-Type": "text/markdown; charset=utf-8"},
    )
    assert response.status_code in (200, 201), response.text
    world.wait()
    instances = world.pool()
    assert len(instances) == 2
    assert {i.template_id for i in instances} == {world.templates()["api"].id}


def test_pool_refills_on_its_own_interval(settings):
    # Пополнение по таймеру идёт по всем текущим шаблонам БД, поэтому тест рассчитан на
    # тестовую БД без чужих шаблонов: шаблоны других тестов удаляются их очисткой.
    generation = GenerationSettings(autostart=True, pool_size=1, pool_refill_interval_seconds=0.05)
    auto = settings.model_copy(update={"generation": generation})
    with TestClient(create_app(auto, llm=FakeLLM(ModelStub()), sandbox=LocalSandbox())) as c:
        world = World(c)
        try:
            # Шаблон появляется после старта: его подхватывает следующий оборот таймера.
            world.add_templates(world.code())
            deadline = time.monotonic() + 10
            while not world.pool() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert len(world.pool()) == 1
        finally:
            world.cleanup()
