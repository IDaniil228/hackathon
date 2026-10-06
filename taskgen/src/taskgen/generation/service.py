"""Тестовая сессия: отбор шаблонов, задания по ним и генерация каждого задания.

Сессия создаётся сразу со всеми заданиями в статусе pending. Первое задание генерируется до
ответа клиенту, остальные — в фоне (worker.py) и появляются в сессии по мере готовности.
Шаблон, который не дал экземпляра, заменяется следующим по вероятности шаблоном того же
грейда и типа из отбора: так замена не меняет ни состав теста по типам, ни квоту
заявленного грейда. Если замены нет или сервис генерации недоступен, сессия получает статус
failed с причиной и больше не генерируется: неполный набор исказил бы оценку грейда.
"""

from __future__ import annotations

import logging
import random
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from taskgen.bank.selector import (
    BankShortageError,
    SelectionError,
    SelectionResult,
    select_templates,
)
from taskgen.config import GenerationSettings, Settings
from taskgen.contracts import (
    Embedder,
    LLMClient,
    RunLimits,
    SandboxRunner,
    SessionStatus,
    TaskStatus,
)
from taskgen.db import AreaSkill, Skill, Task, Template, TestSession
from taskgen.generation import pool
from taskgen.generation.instance import (
    SEED_BITS,
    GenerationError,
    InstanceDeps,
    ModelOutputError,
    Personalization,
    TaskInstance,
    TemplateDefectError,
    generate_instance,
)
from taskgen.generation.profile import CandidateProfile, profile_hash
from taskgen.llm import limited_llm

logger = logging.getLogger(__name__)

# Ключи, которые сессия добавляет к объяснению отбора в test_sessions.selection.
SEED_KEY = "seed"
RANKING_KEY = "ranking"

# Отказ самого шаблона лечится другим шаблоном. Недоступная песочница или модель — нет:
# любой шаблон упадёт так же.
_REPLACEABLE = (TemplateDefectError, ModelOutputError)


class InvalidRequestError(Exception):
    pass


class NotFoundError(Exception):
    pass


class ConflictError(Exception):
    pass


class SessionFailedError(Exception):
    """Первое задание не создано, сессия failed; причина — в cause и в самой сессии."""

    def __init__(self, session_id: uuid.UUID, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.session_id = session_id
        self.cause = cause


class SessionAbortedError(Exception):
    """Задание не создано и не заменено: остальные задания сессии больше не нужны."""

    def __init__(self, reason: str, cause: GenerationError) -> None:
        super().__init__(reason)
        self.reason = reason
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
    profile: CandidateProfile | None = None


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
            checker_model=self.settings.llm.checker_model,
            checker_reasoning_effort=self.settings.llm.checker_reasoning_effort,
            options=self.options,
            limits=RunLimits(
                timeout_ms=sandbox.run_timeout_ms, memory_bytes=sandbox.run_memory_limit_bytes
            ),
        )


# --- Создание сессии ------------------------------------------------------------------------


async def create_session(gen: Generator, spec: SessionSpec) -> uuid.UUID:
    """Отбирает шаблоны и записывает сессию с заданиями в статусе pending."""
    seed = spec.seed if spec.seed is not None else secrets.randbits(SEED_BITS)
    rng = random.Random(seed)
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
                overrides=spec.selection_override,
                profile_skills=spec.profile.skills if spec.profile else (),
                resume=spec.profile.resume if spec.profile else "",
            )
        except SelectionError as error:
            raise InvalidRequestError(f"Отбор невозможен: {error}") from error
        except BankShortageError as error:
            raise ConflictError(f"Тест не собран: {error}") from error
        await _check_skills(db, spec.area, spec.skills)
    if not result.selected:
        raise ConflictError(f"В области {spec.area} нет шаблонов для теста")

    # Seed заданий берутся из того же генератора после отбора: seed сессии задаёт всё.
    task_seeds = [rng.getrandbits(SEED_BITS) for _ in result.selected]
    selection = result.explanation() | {SEED_KEY: seed}
    return await _insert_session(gen, spec, result, selection, task_seeds)


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
    gen: Generator,
    spec: SessionSpec,
    result: SelectionResult,
    selection: dict[str, Any],
    task_seeds: list[int],
) -> uuid.UUID:
    async with gen.sessionmaker() as db, db.begin():
        row = TestSession(
            candidate_ref=spec.candidate_ref,
            area_code=spec.area,
            claimed_grade_code=spec.claimed_grade,
            skills=list(spec.skills),
            temperature=result.parameters.temperature,
            status=SessionStatus.GENERATING,
            selection=selection,
            profile=spec.profile.model_dump(mode="json") if spec.profile else None,
            profile_hash=profile_hash(spec.profile) if spec.profile else None,
        )
        db.add(row)
        await db.flush()
        tasks = [
            Task(
                session_id=row.id,
                template_id=item.template.id,
                position=position,
                status=TaskStatus.PENDING,
                kind=item.template.kind,
                seed=seed,
            )
            for position, (item, seed) in enumerate(
                zip(result.selected, task_seeds, strict=True), start=1
            )
        ]
        # Явный seed обещает воспроизводимую сессию, а экземпляр пула выбран другим seed.
        if spec.seed is None and gen.options.pool_size > 0:
            stock = await pool.take(
                db,
                tasks[0].template_id,
                max_age=timedelta(hours=gen.options.pool_max_age_hours),
            )
            if stock is not None:
                stock.pop("id")
                for name, value in stock.items():
                    setattr(tasks[0], name, value)
                tasks[0].status = TaskStatus.READY
        db.add_all(tasks)
        return row.id


# --- Генерация заданий ----------------------------------------------------------------------


@dataclass(frozen=True)
class PendingTask:
    id: uuid.UUID
    position: int


async def pending_tasks(gen: Generator, session_id: uuid.UUID) -> list[PendingTask]:
    async with gen.sessionmaker() as db:
        rows = await db.execute(
            select(Task.id, Task.position)
            .where(Task.session_id == session_id, Task.status == TaskStatus.PENDING)
            .order_by(Task.position)
        )
        return [PendingTask(task_id, position) for task_id, position in rows]


async def run_task(gen: Generator, task_id: uuid.UUID) -> None:
    """Генерирует задание, при отказе шаблона берёт замену того же грейда и типа.

    SessionAbortedError — задание не создано и не заменено; задание уже помечено failed.
    """
    while True:
        async with gen.sessionmaker() as db:
            row = (
                await db.execute(
                    select(Task, Template, TestSession.profile)
                    .join(Template, Task.template_id == Template.id)
                    .join(TestSession, Task.session_id == TestSession.id)
                    .where(Task.id == task_id)
                )
            ).one_or_none()
            # Задание удалено вместе с сессией или уже обработано: делать нечего.
            if row is None or row[0].status != TaskStatus.PENDING:
                return
            task, template, profile = row
            personalization = await _personalization(db, profile)
        try:
            spec = pool.template_spec(template)
            instance = await generate_instance(
                spec, task.seed, gen.instance_deps(), personalization
            )
        except _REPLACEABLE as error:
            if await _replace(gen, task, template, str(error)):
                continue
            reason = (
                f"Задание {task.position}: {error}. Заменить шаблон нечем: другие шаблоны "
                f"типа {template.kind} грейда {template.grade_code} из отбора уже в сессии, "
                "уже пробовались или исчерпан лимит замен"
            )
            await _fail_task(gen, task_id, reason)
            raise SessionAbortedError(reason, error) from error
        except GenerationError as error:
            reason = f"Задание {task.position}: {error}"
            await _fail_task(gen, task_id, reason)
            raise SessionAbortedError(reason, error) from error
        await _save_instance(gen, task_id, instance)
        return


async def _replace(gen: Generator, task: Task, template: Template, reason: str) -> bool:
    """Ставит в задание следующий по вероятности шаблон того же грейда и типа, которого нет
    в сессии.

    Сессия блокируется на время выбора: два задания, упавшие одновременно, иначе взяли бы
    одну и ту же замену.
    """
    async with gen.sessionmaker() as db, db.begin():
        session = await db.get(TestSession, task.session_id, with_for_update=True)
        current = await db.get(Task, task.id)
        if session is None or current is None:
            return False
        if len(current.replacements) >= gen.options.replacements_per_task:
            return False
        used: set[str] = set()
        rows = await db.execute(
            select(Task.template_id, Task.replacements).where(Task.session_id == session.id)
        )
        for template_id, replaced in rows:
            used.add(str(template_id))
            used.update(item["template_id"] for item in replaced)
        for entry in (session.selection or {}).get(RANKING_KEY, []):
            if (
                entry["grade"] != template.grade_code
                or entry["kind"] != template.kind
                or entry["template_id"] in used
            ):
                continue
            history = {"template_id": str(template.id), "name": template.name, "reason": reason}
            await db.execute(
                update(Task)
                .where(Task.id == task.id)
                .values(
                    template_id=uuid.UUID(entry["template_id"]),
                    kind=entry["kind"],
                    replacements=[*current.replacements, history],
                )
            )
            logger.warning("Задание %s: шаблон %s заменён на %s", task.id, template.name, entry)
            return True
    return False


async def _fail_task(gen: Generator, task_id: uuid.UUID, reason: str) -> None:
    async with gen.sessionmaker() as db, db.begin():
        await db.execute(
            update(Task)
            .where(Task.id == task_id, Task.status == TaskStatus.PENDING)
            .values(status=TaskStatus.FAILED, failure=reason)
        )


async def _save_instance(gen: Generator, task_id: uuid.UUID, instance: TaskInstance) -> None:
    async with gen.sessionmaker() as db, db.begin():
        await db.execute(
            update(Task)
            .where(Task.id == task_id, Task.status == TaskStatus.PENDING)
            .values(
                status=TaskStatus.READY,
                personalization=instance.personalization,
                **instance.columns(),
            )
        )


async def _personalization(
    db: AsyncSession, profile: dict[str, Any] | None
) -> Personalization | None:
    """Профиль из снимка сессии для легенды; навыки — с названиями из справочника."""
    if profile is None:
        return None
    snapshot = CandidateProfile.model_validate(profile)
    titles: dict[str, str] = dict(
        (
            await db.execute(select(Skill.code, Skill.title).where(Skill.code.in_(snapshot.skills)))
        ).all()
    )
    return Personalization(
        resume=snapshot.resume,
        skills=[(code, titles.get(code, code)) for code in snapshot.skills],
        extra=snapshot.extra,
    )


async def finish_session(gen: Generator, session_id: uuid.UUID, failure: str | None = None) -> None:
    """Переводит сессию из generating в ready или, если задан failure, в failed.

    При сбое оставшиеся pending-задания получают failed: генерировать их больше незачем. В
    обоих случаях снимок профиля удаляется: заданий, которым он нужен, больше нет.
    """
    async with gen.sessionmaker() as db, db.begin():
        session = await db.get(TestSession, session_id, with_for_update=True)
        if session is None or session.status != SessionStatus.GENERATING:
            return
        if failure is not None:
            await db.execute(
                update(Task)
                .where(Task.session_id == session_id, Task.status == TaskStatus.PENDING)
                .values(
                    status=TaskStatus.FAILED,
                    failure=f"Задание не создано: генерация сессии остановлена. {failure}",
                )
            )
            session.status = SessionStatus.FAILED
            session.failure = failure
            session.profile = None
            return
        statuses = set(await db.scalars(select(Task.status).where(Task.session_id == session_id)))
        if statuses == {TaskStatus.READY}:
            session.status = SessionStatus.READY
            session.profile = None


async def check_profile(gen: Generator, session_id: uuid.UUID, current_hash: str) -> bool:
    """Сверяет хэш текущего профиля кандидата с хэшем сессии; возвращает profile_changed.

    Изменение только фиксируется: флаг не сбрасывается, а сессия и задания не меняются.
    """
    async with gen.sessionmaker() as db, db.begin():
        session = await db.get(TestSession, session_id, with_for_update=True)
        if session is None:
            raise NotFoundError("Сессия не найдена")
        if current_hash != session.profile_hash:
            session.profile_changed = True
        return session.profile_changed


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
