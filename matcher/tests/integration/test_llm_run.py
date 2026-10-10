"""Прогон с LLM-стадией на подделках через HTTP API: ранжирование, объяснения, отказ модели,
фильтры поверх прогона, уточнение с сообщениями чата, подхват прогонов при старте и фоновый
пересчёт векторов."""

import asyncio
import hashlib
import json
import re
import time
import uuid
from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from matcher.app import create_app
from matcher.config import Settings, VectorSettings
from matcher.db import MatchStage, RunStatus, make_engine, make_sessionmaker
from matcher.llm_stage import llm_stages
from matcher.runs import RunService
from tests.conftest import (
    CHECKER,
    RANKER,
    REWRITER,
    FakeEmbedder,
    FakeLLM,
    honest_checker,
    llm_settings,
    refusing_checker,
)
from tests.integration.conftest import DEMO_NEED, purge_embeddings

ADMIN_KEY = "test-admin-key"
OTHER_NEED = "88888888-0000-4000-8000-000000000002"
NEED_NEGATIVE = "Только фреймворк без понимания данных"

# Кандидаты демо-пула по началу их текста "о себе" (api/app/db/seeds/demo.py).
ANNA = "11111111-0000-4000-8000-000000000001"
KIRILL = "eeeeeeee-0000-4000-8000-000000000001"
ALINA = "eeeeeeee-0000-4000-8000-000000000002"
ABOUT = {
    ANNA: "Backend на Python: сервисы",
    KIRILL: "Python-бэкенд в финтехе",
    ALINA: "Бэкенд на Python и Go",
}
SCORES = {ANNA: 0.9, ALINA: 0.7, KIRILL: 0.2}
ABOUT_HYDE = "Проектирую схемы PostgreSQL и пишу сервисы на Python под нагрузкой"

_BLOCK = re.compile(
    r"Кандидат (\d+)\n<<<КАНДИДАТ-(\w+)>>>\n(.*?)\n<<<КОНЕЦ-КАНДИДАТА-\2>>>", re.DOTALL
)


def rewriter(messages, schema) -> str:
    return json.dumps(
        {
            "skills": [{"name": "Python", "level": 4}],
            "experience_months": 36,
            "about": ABOUT_HYDE,
            "test_results": "",
        },
        ensure_ascii=False,
    )


def ranker(messages, schema) -> str:
    """Анна лучше всех, Кирилл нарушает негативный промт работодателя, остальные посередине."""
    rows = []
    for number, _, block in _BLOCK.findall(messages[1].content):
        who = next((cid for cid, about in ABOUT.items() if about in block), None)
        rows.append(
            {
                "candidate": int(number),
                "score": SCORES.get(who, 0.5),
                "need_negative_hits": [NEED_NEGATIVE] if who == KIRILL else [],
                "candidate_negative_hits": [],
                "sentences": ["Грейд подтверждён тестом."],
            }
        )
    return json.dumps({"candidates": rows}, ensure_ascii=False)


def summary_writer_or_ranker(messages, schema) -> str:
    if "candidates" in (schema or {}).get("properties", {}):
        return ranker(messages, schema)
    return json.dumps({"sentences": ["Подборка собрана."]}, ensure_ascii=False)


def down(messages, schema) -> str:
    raise httpx.ConnectError("сеть недоступна")


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def _scalar(url: str, query: str, **params):
    engine = make_engine(url)
    try:
        async with engine.connect() as connection:
            return (await connection.execute(text(query), params)).scalar()
    finally:
        await engine.dispose()


async def _purge(url: str, runs: list[str], since_message: int, threads_before: list) -> None:
    engine = make_engine(url)
    async with engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM match.chat_messages WHERE id > :id"), {"id": since_message}
        )
        await connection.execute(
            text("DELETE FROM match.chat_threads WHERE NOT (id = ANY(:keep))"),
            {"keep": threads_before},
        )
        await connection.execute(
            text("DELETE FROM match.search_runs WHERE id = ANY(:ids)"),
            {"ids": [uuid.UUID(run) for run in runs]},
        )
        await connection.execute(text("DELETE FROM matcher.api_keys WHERE name LIKE 'test-%'"))
        await purge_embeddings(connection, FakeEmbedder().model_name)
    await engine.dispose()


@pytest.fixture
def app_factory(database_url: str, owner_url: str) -> Iterator[Callable]:
    """Приложение на подделках; всё, что тест создал в базе, удаляется после него."""
    created: list[str] = []
    since_message = asyncio.run(
        _scalar(owner_url, "SELECT coalesce(max(id), 0) FROM match.chat_messages")
    )
    threads = asyncio.run(
        _scalar(owner_url, "SELECT coalesce(array_agg(id), '{}') FROM match.chat_threads")
    )
    clients: list[TestClient] = []

    def make(llm: FakeLLM | None, **fields) -> TestClient:
        settings = (llm_settings if llm is not None else Settings)(
            database_url=database_url, bootstrap_admin_key=ADMIN_KEY, **fields
        )
        client = TestClient(create_app(settings, llm=llm, embedder=FakeEmbedder()))
        client.__enter__()
        client.created = created
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)
    asyncio.run(_purge(owner_url, created, since_message, list(threads)))


def _key(client: TestClient, role: str = "client") -> str:
    response = client.post(
        "/keys", json={"name": f"test-{role}", "role": role}, headers=_headers(ADMIN_KEY)
    )
    assert response.status_code == 201, response.text
    return response.json()["key"]


def _wait(client: TestClient, key: str, run_id: str) -> dict:
    deadline = time.monotonic() + 30
    while True:
        run = client.get(f"/runs/{run_id}", headers=_headers(key)).json()
        if run["status"] in (RunStatus.DONE, RunStatus.FAILED) or time.monotonic() > deadline:
            return run
        time.sleep(0.05)


def _start(client: TestClient, key: str, need: str = DEMO_NEED) -> dict:
    response = client.post(f"/needs/{need}/runs", headers=_headers(key))
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    client.created.append(run_id)
    return _wait(client, key, run_id)


def _llm(**roles) -> FakeLLM:
    return FakeLLM(
        {REWRITER: rewriter, RANKER: summary_writer_or_ranker, CHECKER: honest_checker} | roles
    )


def test_llm_run_ranks_by_llm_score_and_keeps_verified_explanations(app_factory) -> None:
    llm = _llm()
    client = app_factory(llm)
    key = _key(client)
    run = _start(client, key)

    assert run["status"] == RunStatus.DONE, run
    assert run["strategy"] == "prefilter_vector_llm" and run["llm_model"] == RANKER
    params = run["params"]
    assert params["query_variant"] == "hyde" and params["llm_fallback"] is False
    assert ABOUT_HYDE in params["query_profile"] and NEED_NEGATIVE not in params["query_profile"]
    # Переписчик не видит негативного промта, ранжировщик видит.
    (rewrite_call,) = llm.calls_of(REWRITER)
    assert NEED_NEGATIVE not in "\n".join(m.content for m in rewrite_call["messages"])
    assert all(NEED_NEGATIVE in c["messages"][1].content for c in llm.calls_of(RANKER)[:1])

    results = run["results"]
    by_id = {row["candidate_id"]: row for row in results}
    assert all(row["stage"] == MatchStage.LLM for row in results)
    assert by_id[KIRILL]["factors"]["llm"]["need_negative_hits"] == [NEED_NEGATIVE]
    assert by_id[ANNA]["factors"]["llm"]["need_negative_hits"] == []
    # Внутри подтверждённых порядок задаёт llm_score, а не factor_score.
    confirmed = [row["llm_score"] for row in results if row["factors"]["grade_confirmed"]]
    assert confirmed == sorted(confirmed, reverse=True)
    assert by_id[ANNA]["rank"] < by_id[KIRILL]["rank"]
    assert by_id[ALINA]["rank"] < by_id[KIRILL]["rank"]
    assert by_id[ANNA]["explanation"] == "Грейд подтверждён тестом."
    assert run["summary"] == "Подборка собрана."


def test_checker_rejection_leaves_the_template(app_factory) -> None:
    client = app_factory(_llm(**{CHECKER: refusing_checker}))
    key = _key(client)
    run = _start(client, key)
    assert run["status"] == RunStatus.DONE, run
    for row in run["results"]:
        assert row["explanation"] != "Грейд подтверждён тестом."
        assert "Стек:" in row["explanation"]
    assert run["summary"] and "Подборка собрана." not in run["summary"]


def test_llm_outage_orders_by_factors_with_templates(app_factory) -> None:
    client = app_factory(_llm(**{REWRITER: down, RANKER: down, CHECKER: down}))
    key = _key(client)
    run = _start(client, key)

    assert run["status"] == RunStatus.DONE, run
    assert run["llm_model"] is None
    assert run["params"]["llm_fallback"] is True and run["params"]["query_variant"] == "structured"
    results = run["results"]
    assert all(row["llm_score"] is None and "llm" not in row["factors"] for row in results)
    assert all(row["stage"] != MatchStage.LLM and "Стек:" in row["explanation"] for row in results)
    keys = [(not r["factors"]["grade_confirmed"], -r["factors"]["factor_score"]) for r in results]
    assert keys == sorted(keys)


def test_hyde_card_hash_keys_the_need_vector(app_factory, database_url: str) -> None:
    client = app_factory(_llm(), vector=VectorSettings(top_k=2))
    key = _key(client)
    run = _start(client, key)
    assert run["status"] == RunStatus.DONE, run
    params = run["params"]
    digest = hashlib.sha256(params["query_profile"].encode()).hexdigest()
    assert params["query_hash"] == digest
    stored = asyncio.run(
        _scalar(
            database_url,
            "SELECT source_hash FROM match.need_embeddings WHERE need_id = :n AND model = :m",
            n=uuid.UUID(DEMO_NEED),
            m=FakeEmbedder().model_name,
        )
    )
    assert stored == digest


async def _counts(url: str) -> tuple:
    engine = make_engine(url)
    async with engine.connect() as connection:
        counts = []
        for table in ("match.search_runs", "match.search_results", "match.chat_messages"):
            result = await connection.execute(text(f"SELECT count(*) FROM {table}"))
            counts.append(result.scalar())
    await engine.dispose()
    return tuple(counts)


def test_results_filters_narrow_a_finished_run_and_write_nothing(
    app_factory, database_url: str
) -> None:
    client = app_factory(None)
    key = _key(client)
    run = _start(client, key)
    run_id = run["id"]
    total = len(run["results"])
    before = asyncio.run(_counts(database_url))

    def get(**query) -> dict:
        response = client.get(f"/runs/{run_id}/results", params=query, headers=_headers(key))
        assert response.status_code == 200, response.text
        return response.json()

    everything = get()
    assert everything["total"] == everything["matched"] == total
    assert [row["rank"] for row in everything["results"]] == sorted(
        row["rank"] for row in run["results"]
    )
    moscow = get(city="москва")
    assert moscow["total"] == total and 0 < moscow["matched"] < total
    assert ANNA in {row["candidate_id"] for row in moscow["results"]}
    confirmed = get(grade_confirmed="true")
    assert confirmed["matched"] == sum(r["factors"]["grade_confirmed"] for r in run["results"])
    assert get(skills=["python"])["matched"] <= total
    assert get(skills=["python", "no-such-skill"])["matched"] == 0
    assert get(salary_max=1)["matched"] == 0
    scores = sorted(row["final_score"] for row in run["results"])
    threshold = scores[len(scores) // 2]
    assert get(min_score=threshold)["matched"] == sum(s >= threshold for s in scores)
    assert get(min_score=0.0)["matched"] == total
    assert get(relocation="true", city="москва")["matched"] <= moscow["matched"]
    brief = get(view="brief")
    assert all(set(row) == {"candidate_id", "rank", "final_score"} for row in brief["results"])
    assert asyncio.run(_counts(database_url)) == before
    assert client.get(f"/runs/{uuid.uuid4()}/results", headers=_headers(key)).status_code == 404


def test_refine_chains_messages_and_writes_chat(app_factory, owner_url: str) -> None:
    llm = _llm()
    client = app_factory(llm)
    key = _key(client)
    first = _start(client, key)

    def refine(run_id: str, message: str) -> dict:
        response = client.post(
            f"/runs/{run_id}/refine", json={"message": message}, headers=_headers(key)
        )
        assert response.status_code == 202, response.text
        new_id = response.json()["run_id"]
        client.created.append(new_id)
        return _wait(client, key, new_id)

    second = refine(first["id"], "Нужен опыт с Kafka")
    third = refine(second["id"], "Только Москва")
    assert second["status"] == third["status"] == RunStatus.DONE
    assert second["parent_run_id"] == first["id"] and third["parent_run_id"] == second["id"]
    assert third["params"]["refinements"] == ["Нужен опыт с Kafka", "Только Москва"]
    last_rewrite = llm.calls_of(REWRITER)[-1]["messages"][1].content
    assert "Нужен опыт с Kafka" in last_rewrite and "Только Москва" in last_rewrite

    rows = asyncio.run(_chat(owner_url, DEMO_NEED))
    assert rows[-4:] == [
        ("user", None, "Нужен опыт с Kafka"),
        ("assistant", second["id"], None),
        ("user", None, "Только Москва"),
        ("assistant", third["id"], None),
    ]
    missing = client.post(
        f"/runs/{uuid.uuid4()}/refine", json={"message": "x"}, headers=_headers(key)
    )
    assert missing.status_code == 404


async def _chat(url: str, need: str) -> list[tuple]:
    engine = make_engine(url)
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(
                """
                SELECT m.role::text, m.run_id::text, m.content
                FROM match.chat_messages m JOIN match.chat_threads t ON t.id = m.thread_id
                WHERE t.need_id = :need ORDER BY m.id
                """
            ),
            {"need": uuid.UUID(need)},
        )
        result = [(r[0], r[1], r[2] if r[0] == "user" else None) for r in rows]
    await engine.dispose()
    return result


def test_refine_creates_the_thread_of_a_need_without_one(app_factory, owner_url: str) -> None:
    client = app_factory(_llm())
    key = _key(client)
    first = _start(client, key, need=OTHER_NEED)
    assert first["status"] == RunStatus.DONE, first
    assert asyncio.run(_chat(owner_url, OTHER_NEED)) == []
    response = client.post(
        f"/runs/{first['id']}/refine", json={"message": "Добавь RAG"}, headers=_headers(key)
    )
    assert response.status_code == 202, response.text
    client.created.append(response.json()["run_id"])
    _wait(client, key, response.json()["run_id"])
    assert [row[0] for row in asyncio.run(_chat(owner_url, OTHER_NEED))] == ["user", "assistant"]


def test_startup_picks_up_runs_left_queued(app_factory, database_url: str) -> None:
    async def queue() -> str:
        engine = make_engine(database_url)
        service = RunService(
            make_sessionmaker(engine), Settings(database_url=database_url), embedder=None
        )
        run_id = await service.create_run(uuid.UUID(DEMO_NEED))
        await engine.dispose()
        return str(run_id)

    run_id = asyncio.run(queue())
    client = app_factory(None)
    client.created.append(run_id)
    key = _key(client)
    assert _wait(client, key, run_id)["status"] == RunStatus.DONE


def test_embeddings_refresh_is_admin_only_and_runs_in_background(
    app_factory, database_url: str
) -> None:
    client = app_factory(None)
    key = _key(client)
    assert client.post("/embeddings/refresh", headers=_headers(key)).status_code == 403
    response = client.post("/embeddings/refresh", headers=_headers(ADMIN_KEY))
    assert response.status_code == 202, response.text
    query = "SELECT count(*) FROM match.candidate_embeddings WHERE model = :m"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if asyncio.run(_scalar(database_url, query, m=FakeEmbedder().model_name)):
            break
        time.sleep(0.05)
    assert asyncio.run(_scalar(database_url, query, m=FakeEmbedder().model_name)) > 0


def test_no_connection_is_held_while_models_and_taskgen_answer(database_url: str) -> None:
    """Ответа модели на локальном железе ждут минутами. Соединение, занятое на это время,
    при десятке одновременных подборов исчерпывает пул и общий лимит Postgres."""

    async def scenario() -> tuple[RunStatus, list[tuple[str, int]]]:
        engine = make_engine(database_url)
        held: list[tuple[str, int]] = []

        def watch(role: str, responder: Callable) -> Callable:
            def answer(messages, schema) -> str:
                held.append((role, engine.pool.checkedout()))
                return responder(messages, schema)

            return answer

        class WatchedEmbedder(FakeEmbedder):
            async def embed(self, texts: list[str]) -> list[list[float]]:
                held.append(("embedding", engine.pool.checkedout()))
                return await super().embed(texts)

        class WatchedSummaries:
            async def summary(self, candidate_ref: str) -> str | None:
                held.append(("summary", engine.pool.checkedout()))
                return None

        llm = FakeLLM(
            {
                REWRITER: watch("rewriter", rewriter),
                RANKER: watch("ranker", ranker),
                CHECKER: watch("checker", honest_checker),
            }
        )
        settings = llm_settings(database_url=database_url, vector=VectorSettings(top_k=1))
        embedder = WatchedEmbedder(model="fake-watched")
        service = RunService(
            make_sessionmaker(engine),
            settings,
            embedder=embedder,
            summaries=WatchedSummaries(),
            stages=llm_stages(llm, settings),
        )
        run_id = await service.create_run(uuid.UUID(DEMO_NEED))
        try:
            status = await service.execute(run_id)
        finally:
            async with engine.begin() as connection:
                await connection.execute(
                    text("DELETE FROM match.search_runs WHERE id = :id"), {"id": run_id}
                )
                await purge_embeddings(connection, embedder.model_name)
            await engine.dispose()
        return status, held

    status, held = asyncio.run(scenario())
    assert status == RunStatus.DONE
    assert {role for role, _ in held} == {
        "rewriter",
        "ranker",
        "checker",
        "embedding",
        "summary",
    }
    assert [(role, busy) for role, busy in held if busy] == []
