"""Тестовая сессия: отбор шаблонов и генерация заданий по ним.

Сессия выдаётся целиком или не выдаётся: если не удалось создать хотя бы одно задание, сессия
получает статус failed с причиной и остаётся без заданий. Неполный набор исказил бы оценку
грейда по уровням.
"""

from __future__ import annotations

import asyncio
import logging
import random
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from taskgen.bank.parser import TemplateError, parse_template
from taskgen.bank.selector import SelectionError, SelectionResult, select_templates
from taskgen.config import GenerationSettings, Settings
from taskgen.contracts import Embedder, LLMClient, RunLimits, SandboxRunner
from taskgen.db import AreaSkill, Skill, Task, TestSession
from taskgen.generation.instance import (
    GenerationError,
    InstanceDeps,
    LLMUnavailableError,
    ModelOutputError,
    SandboxUnavailableError,
    TaskInstance,
    TemplateDefectError,
    generate_instance,
)
from taskgen.llm import limited_llm

logger = logging.getLogger(__name__)

GENERATING = "generating"
READY = "ready"
FAILED = "failed"

# Ключи, которые сессия добавляет к объяснению отбора в test_sessions.selection.
SEED_KEY = "seed"
FAILURE_KEY = "failure"

# seed хранится в tasks.seed типа BIGINT со знаком.
SEED_BITS = 63

# Какую ошибку назвать клиенту, если задания сессии упали по разным причинам: недоступный
# сервис чинится повтором, а дефект шаблона — только правкой банка.
_ERROR_PRIORITY = (
    SandboxUnavailableError,
    LLMUnavailableError,
    TemplateDefectError,
    ModelOutputError,
)


class InvalidRequestError(Exception):
    pass


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    pass


class SessionFailedError(Exception):
    """Сессия создана, но задания не сгенерированы; причина — в cause и в самой сессии."""

    def __init__(self, session_id: uuid.UUID, cause: GenerationError) -> None:
        super().__init__(str(cause))
        self.session_id = session_id
        self.cause = cause


@dataclass(frozen=True)
class SessionSpec:
    candidate_ref: str
    area: str
    claimed_grade: str
    skills: list[str]
    seed: int | None = None
    # Поля SelectionSettings; задаются только admin-ключом, проверяет это API.
    selection_override: dict[str, Any] | None = None


@dataclass
class Generator:
    settings: Settings
    options: GenerationSettings
    sessionmaker: async_sessionmaker[AsyncSession]
    llm: LLMClient
    embedder: Embedder | None
    sandbox: SandboxRunner | None

    @classmethod
    def from_state(cls, state: Any) -> Generator:
        """Собирает зависимости из app.state так же, как Evaluator.from_state."""
        settings: Settings = state.settings
        # Без отдельного эмбеддера эмбеддинги считает LLM-клиент, как в API банка. Клиент без
        # embed не эмбеддер: отбор тогда сразу идёт по доле общих навыков.
        embedder = getattr(state, "embedder", None) or state.llm
        if not (hasattr(embedder, "embed") and hasattr(embedder, "model_name")):
            embedder = None
        return cls(
            settings=settings,
            options=settings.generation,
            sessionmaker=state.sessionmaker,
            llm=limited_llm(state),
            embedder=embedder,
            sandbox=getattr(state, "sandbox", None),
        )

    def instance_deps(self) -> InstanceDeps:
        sandbox = self.settings.sandbox
        return InstanceDeps(
            llm=self.llm,
            sandbox=self.sandbox,
            model=self.settings.llm.generator_model,
            reasoning_effort=self.settings.llm.generator_reasoning_effort,
            options=self.options,
            limits=RunLimits(
                timeout_ms=sandbox.run_timeout_ms, memory_bytes=sandbox.run_memory_limit_bytes
            ),
        )


# --- Создание сессии ------------------------------------------------------------------------


async def create_session(gen: Generator, spec: SessionSpec) -> uuid.UUID:
    seed = spec.seed if spec.seed is not None else secrets.randbits(SEED_BITS)
    rng = random.Random(seed)
    override = spec.selection_override or {}
    async with gen.sessionmaker() as db:
        try:
            result = await select_templates(
                db,
                area=spec.area,
                grade=spec.claimed_grade,
                skills=spec.skills,
                rng=rng,
                settings=gen.settings.selection,
                embedder=gen.embedder,
                k=override.get("tasks_per_test"),
                temperature=override.get("temperature"),
                alpha=override.get("alpha"),
                beta=override.get("beta"),
            )
        except SelectionError as error:
            raise InvalidRequestError(f"Отбор невозможен: {error}") from error
        await _check_skills(db, spec.area, spec.skills)
    if not result.selected:
        raise ConflictError(f"В области {spec.area} нет шаблонов для теста")

    # Seed заданий берутся из того же генератора после отбора: seed сессии задаёт всё.
    task_seeds = [rng.getrandbits(SEED_BITS) for _ in result.selected]
    selection = result.explanation() | {SEED_KEY: seed}
    session_id = await _insert_session(gen, spec, result, selection)
    try:
        instances = await _generate_all(gen, result, task_seeds)
    except GenerationError as error:
        await _mark_failed(gen, session_id, selection, str(error))
        raise SessionFailedError(session_id, error) from error
    except Exception:
        await _mark_failed(gen, session_id, selection, "внутренняя ошибка генерации")
        raise

    async with gen.sessionmaker() as db, db.begin():
        for position, (item, instance) in enumerate(
            zip(result.selected, instances, strict=True), start=1
        ):
            db.add(_task_row(session_id, item.template.id, position, instance))
        await db.execute(
            update(TestSession).where(TestSession.id == session_id).values(status=READY)
        )
    return session_id


async def _check_skills(db: AsyncSession, area: str, skills: list[str]) -> None:
    known = set(await db.scalars(select(Skill.code).where(Skill.code.in_(skills))))
    unknown = [code for code in skills if code not in known]
    if unknown:
        raise InvalidRequestError(f"Нет навыков в справочнике: {', '.join(unknown)}")
    linked = set(
        await db.scalars(
            select(AreaSkill.skill_code).where(
                AreaSkill.area_code == area, AreaSkill.skill_code.in_(skills)
            )
        )
    )
    foreign = [code for code in skills if code not in linked]
    if foreign:
        raise InvalidRequestError(f"Навыки не относятся к области {area}: {', '.join(foreign)}")


async def _insert_session(
    gen: Generator, spec: SessionSpec, result: SelectionResult, selection: dict[str, Any]
) -> uuid.UUID:
    async with gen.sessionmaker() as db, db.begin():
        row = TestSession(
            candidate_ref=spec.candidate_ref,
            area_code=spec.area,
            claimed_grade_code=spec.claimed_grade,
            skills=list(spec.skills),
            temperature=result.parameters.temperature,
            status=GENERATING,
            selection=selection,
        )
        db.add(row)
        await db.flush()
        return row.id


async def _mark_failed(
    gen: Generator, session_id: uuid.UUID, selection: dict[str, Any], reason: str
) -> None:
    # Колонки для причины в test_sessions нет, поэтому она хранится рядом с объяснением отбора.
    async with gen.sessionmaker() as db, db.begin():
        await db.execute(
            update(TestSession)
            .where(TestSession.id == session_id)
            .values(status=FAILED, selection=selection | {FAILURE_KEY: reason})
        )


async def _generate_all(
    gen: Generator, result: SelectionResult, seeds: list[int]
) -> list[TaskInstance]:
    specs = []
    for item in result.selected:
        try:
            specs.append(parse_template(item.template.body, source=item.template.name))
        except TemplateError as error:
            message = f"Шаблон {item.template.name} не разбирается: {error}"
            raise TemplateDefectError(message) from error
    deps = gen.instance_deps()
    try:
        # Задания генерируются параллельно; число вызовов LLM ограничивает LimitedLLM.
        # TaskGroup отменяет остальные задания после первой ошибки: сессия всё равно не выйдет.
        async with asyncio.TaskGroup() as group:
            jobs = [
                group.create_task(generate_instance(spec, seed, deps))
                for spec, seed in zip(specs, seeds, strict=True)
            ]
    except* GenerationError as errors:
        raise _most_relevant(list(errors.exceptions)) from None
    return [job.result() for job in jobs]


def _most_relevant(errors: list[BaseException]) -> BaseException:
    for kind in _ERROR_PRIORITY:
        for error in errors:
            if isinstance(error, kind):
                return error
    return errors[0]


def _task_row(
    session_id: uuid.UUID, template_id: uuid.UUID, position: int, instance: TaskInstance
) -> Task:
    return Task(
        session_id=session_id,
        template_id=template_id,
        position=position,
        kind=instance.kind.value,
        statement=instance.statement,
        params=instance.params,
        seed=instance.seed,
        public_payload=instance.public_dump(),
        hidden_payload=instance.hidden_dump(),
        generated_by=instance.generated_by,
        validated=instance.validated,
    )


# --- Чтение ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionView:
    session: TestSession
    tasks: list[Task]


async def get_session(gen: Generator, session_id: uuid.UUID) -> SessionView:
    async with gen.sessionmaker() as db:
        session = await db.get(TestSession, session_id)
        if session is None:
            raise NotFoundError("Сессия не найдена")
        tasks = await db.scalars(
            select(Task).where(Task.session_id == session_id).order_by(Task.position)
        )
        return SessionView(session, list(tasks))


async def get_task(gen: Generator, task_id: uuid.UUID) -> Task:
    async with gen.sessionmaker() as db:
        task = await db.get(Task, task_id)
    if task is None:
        raise NotFoundError("Задание не найдено")
    return task
