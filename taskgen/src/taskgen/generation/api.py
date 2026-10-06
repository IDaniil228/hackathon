"""HTTP API тестовых сессий. Логика — в service.py и worker.py, здесь только перевод в HTTP.

Наружу уходит только публичная часть заданий: hidden_payload не попадает ни в один ответ,
потому что модели ответов его не содержат.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from taskgen.auth import AUTH_ERRORS, Role, require
from taskgen.bank.selection import Slot
from taskgen.contracts import SessionStatus, TaskKind, TaskStatus
from taskgen.db import Task
from taskgen.generation import service
from taskgen.generation.instance import (
    LLMUnavailableError,
    ModelOutputError,
    PublicPayload,
    SandboxUnavailableError,
    TemplateDefectError,
)
from taskgen.generation.profile import CandidateProfile, profile_hash
from taskgen.generation.service import Generator, SessionSpec
from taskgen.generation.worker import GenerationWorker

router = APIRouter(tags=["Тестовые сессии"], responses=AUTH_ERRORS)

_FAILURE_STATUS = {
    SandboxUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    LLMUnavailableError: status.HTTP_502_BAD_GATEWAY,
    ModelOutputError: status.HTTP_502_BAD_GATEWAY,
    TemplateDefectError: status.HTTP_500_INTERNAL_SERVER_ERROR,
}


def _generator(request: Request) -> Generator:
    return Generator.from_state(request.app.state)


def _worker(request: Request) -> GenerationWorker:
    return request.app.state.generation_worker


# --- Модели ---------------------------------------------------------------------------------


class SelectionOverride(BaseModel):
    """Параметры отбора вместо test_configs и настроек; пустое поле не переопределяется."""

    model_config = ConfigDict(extra="forbid")

    temperature: float | None = Field(default=None, description="Температура отбора, 0..1")
    alpha: float | None = Field(default=None, description="Вес разницы грейдов")
    beta: float | None = Field(default=None, description="Вес несходства навыков")
    code_tasks: int | None = Field(default=None, description="Число заданий с кодом")
    essay_tasks: int | None = Field(default=None, description="Число эссе")
    quiz_tasks: int | None = Field(default=None, description="Число тестовых задачек")
    min_claimed_per_kind: int | None = Field(
        default=None, description="Квота заданий заявленного грейда в каждом типе"
    )


class SessionCreate(BaseModel):
    # Лишние поля запрещены: параметры отбора клиент не передаёт, их задают test_configs.
    model_config = ConfigDict(extra="forbid")

    candidate_ref: str = Field(
        min_length=1, max_length=100, description="Идентификатор кандидата в основном бэкенде"
    )
    area: str = Field(description="Код области")
    claimed_grade: str = Field(description="Код грейда, на который претендует кандидат")
    skills: list[str] = Field(description="Коды навыков кандидата из справочника области")
    seed: int | None = Field(
        default=None,
        ge=0,
        lt=2**service.SEED_BITS,
        description="Seed отбора и генерации; тот же seed на том же банке даёт те же задания",
    )
    selection_override: SelectionOverride | None = Field(
        default=None, description="Только для роли admin: параметры отбора для этой сессии"
    )
    profile: CandidateProfile | None = Field(
        default=None,
        description="Профиль кандидата. Влияет на отбор шаблонов и тематику легенды, но не на "
        "сложность. Хранится в сессии, пока генерируются её задания, затем удаляется; "
        "остаётся только хэш",
    )


class ProfileCheck(BaseModel):
    """Текущий профиль кандидата или его хэш; ровно одно из двух полей."""

    model_config = ConfigDict(extra="forbid")

    profile: CandidateProfile | None = Field(
        default=None, description="Текущий профиль; хэш сервис посчитает сам"
    )
    profile_hash: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description="SHA-256 канонического JSON профиля: ключи по алфавиту, без пробелов, "
        "UTF-8, все поля профиля со значениями по умолчанию. Надёжнее передать profile",
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> ProfileCheck:
        if (self.profile is None) == (self.profile_hash is None):
            raise ValueError("Нужно ровно одно поле: profile или profile_hash")
        return self


class ProfileCheckOut(BaseModel):
    profile_changed: bool = Field(
        description="Профиль кандидата отличается от того, с которым создана сессия; флаг "
        "только фиксирует факт и не сбрасывается"
    )


class TaskOut(BaseModel):
    """Задание глазами кандидата: условие и публичная часть, без скрытых тестов и рубрики."""

    id: uuid.UUID
    session_id: uuid.UUID
    position: int = Field(description="Номер задания в сессии, с 1")
    status: TaskStatus = Field(
        description="pending — задание ещё создаётся, ready — готово, failed — не создано"
    )
    kind: TaskKind
    statement: str | None = Field(description="Текст условия в Markdown; есть только у ready")
    public: PublicPayload | None = Field(description="Публичная часть; есть только у ready")
    failure: str | None = Field(description="Почему задание не создано")

    @classmethod
    def of(cls, task: Task) -> TaskOut:
        ready = task.status == TaskStatus.READY
        return cls(
            id=task.id,
            session_id=task.session_id,
            position=task.position,
            status=TaskStatus(task.status),
            kind=TaskKind(task.kind),
            statement=task.statement if ready else None,
            public=PublicPayload.model_validate(task.public_payload) if ready else None,
            failure=task.failure,
        )


class SessionOut(BaseModel):
    id: uuid.UUID
    candidate_ref: str
    area: str
    claimed_grade: str
    skills: list[str]
    status: SessionStatus = Field(
        description="generating — первое задание выдано, остальные создаются; ready — все "
        "задания готовы; failed — набор заданий не собран, причина в failure; scored — "
        "сессия оценена"
    )
    failure: str | None = Field(description="Причина статуса failed")
    assigned_grade: str | None = Field(description="Грейд по итогам оценки; null — до оценки")
    profile_hash: str | None = Field(description="Хэш профиля, с которым создана сессия")
    profile_changed: bool = Field(description="Основной бэкенд сообщил о другом профиле")
    created_at: datetime
    tasks: list[TaskOut] = Field(description="Все задания сессии по порядку, с их статусами")

    @classmethod
    def of(cls, view: service.SessionView) -> SessionOut:
        session = view.session
        result = session.result or {}
        return cls(
            id=session.id,
            candidate_ref=session.candidate_ref,
            area=session.area_code,
            claimed_grade=session.claimed_grade_code,
            skills=list(session.skills),
            status=SessionStatus(session.status),
            failure=session.failure,
            assigned_grade=result.get("assigned_grade"),
            profile_hash=session.profile_hash,
            profile_changed=session.profile_changed,
            created_at=session.created_at,
            tasks=[TaskOut.of(task) for task in view.tasks],
        )


class SelectedOut(BaseModel):
    template_id: uuid.UUID
    name: str
    version: int
    grade: str
    kind: TaskKind
    slot: Slot = Field(
        description="quota — слот квоты, выбран только среди шаблонов заявленного грейда "
        "своего типа; explore — разведочный слот, выбран среди всех шаблонов типа"
    )
    grade_gap: int = Field(description="Разность позиций грейда шаблона и заявленного")
    similarity: float = Field(description="Сходство шаблона с навыками кандидата")
    distance: float
    probability: float = Field(
        description="Вероятность выпасть первым из пула своего слота: шаблонов заявленного "
        "грейда этого типа для quota, всех шаблонов типа для explore"
    )
    step_probability: float = Field(description="Вероятность на том шаге, где шаблон выпал")


class ReplacementOut(BaseModel):
    position: int = Field(description="Номер задания, в котором заменён шаблон")
    template_id: uuid.UUID = Field(description="Заменённый шаблон")
    name: str
    reason: str = Field(description="Почему шаблон не дал экземпляра")


class SelectionOut(BaseModel):
    session_id: uuid.UUID
    seed: int
    selection_mode: str = Field(description="embeddings или skills_jaccard")
    fallback_reason: str | None = Field(description="Почему отбор шёл без эмбеддингов")
    parameters: dict[str, Any] = Field(description="Параметры отбора, с которыми он шёл")
    candidates: int = Field(description="Сколько шаблонов области участвовало в отборе")
    selected: list[SelectedOut] = Field(description="Выбранные шаблоны в порядке заданий")
    replacements: list[ReplacementOut] = Field(
        description="Шаблоны, которые не дали экземпляра и заменены следующим по вероятности "
        "шаблоном того же грейда и типа; в порядке замен"
    )


# --- Эндпоинты ------------------------------------------------------------------------------


@router.post(
    "/sessions",
    response_model=SessionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать тестовую сессию",
    description=(
        "Отбирает шаблоны области по заявленному грейду и навыкам кандидата отдельно для "
        "каждого типа заданий и создаёт сессию со всеми заданиями. Первые задания каждого "
        "типа, по квоте min_claimed_per_kind, всегда заявленного грейда. Ответ приходит, "
        "когда готово первое задание; остальные создаются в фоне и появляются в "
        "GET /sessions/{id} по мере готовности, сессия при этом в статусе generating. "
        "Задание с кодом выдаётся только после того, как эталон прошёл все его тесты в "
        "песочнице. Шаблон, который не дал экземпляра, заменяется другим того же грейда и "
        "типа. Параметры отбора берутся из настроек теста; переопределить их может только "
        "admin. Если первое задание создать не удалось, сессия остаётся со "
        "статусом failed, а detail ответа содержит message и session_id"
    ),
    responses={
        403: {"description": "Ключ не имеет нужной роли или client передал selection_override"},
        409: {
            "description": "В области нет шаблонов или шаблонов заявленного грейда какого-то "
            "типа меньше квоты min_claimed_per_kind; detail называет тип и число"
        },
        422: {"description": "Неизвестная область, грейд или навык; неверные параметры отбора"},
        500: {"description": "Шаблон первого задания и его замены не дали экземпляра"},
        502: {"description": "Модель генерации недоступна или не написала условие"},
        503: {"description": "Песочница не настроена или недоступна; сессия failed"},
    },
)
async def create_session(
    body: SessionCreate,
    request: Request,
    response: Response,
    role: Annotated[Role, Depends(require(Role.CLIENT))],
) -> SessionOut:
    if body.selection_override is not None and role is not Role.ADMIN:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Переопределять параметры отбора может только admin"
        )
    gen = _generator(request)
    spec = SessionSpec(
        candidate_ref=body.candidate_ref,
        area=body.area,
        claimed_grade=body.claimed_grade,
        skills=body.skills,
        seed=body.seed,
        selection_override=body.selection_override.model_dump(exclude_none=True)
        if body.selection_override
        else None,
        profile=body.profile,
    )
    try:
        session_id = await service.create_session(gen, spec)
    except service.InvalidRequestError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    except service.ConflictError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    try:
        await _worker(request).start(session_id)
    except service.SessionFailedError as error:
        code = _FAILURE_STATUS.get(type(error.cause), status.HTTP_500_INTERNAL_SERVER_ERROR)
        detail = {"message": str(error.cause), "session_id": str(error.session_id)}
        raise HTTPException(code, detail) from error
    response.headers["Location"] = f"/sessions/{session_id}"
    return SessionOut.of(await service.get_session(gen, session_id))


@router.get(
    "/sessions/{session_id}",
    response_model=SessionOut,
    summary="Тестовая сессия",
    description=(
        "Статус сессии и её задания: условие и публичная часть, без скрытых тестов. Пока "
        "сессия в статусе generating, клиент опрашивает этот эндпоинт и получает задания по "
        "мере готовности"
    ),
    responses={404: {"description": "Сессия не найдена"}},
    dependencies=[Depends(require(Role.CLIENT))],
)
async def get_session(session_id: uuid.UUID, request: Request) -> SessionOut:
    try:
        return SessionOut.of(await service.get_session(_generator(request), session_id))
    except service.NotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.post(
    "/sessions/{session_id}/profile",
    response_model=ProfileCheckOut,
    summary="Сверить профиль кандидата с профилем сессии",
    description=(
        "Основной бэкенд передаёт текущий профиль кандидата или его хэш. Если хэш отличается "
        "от хэша профиля, с которым создана сессия, сессия помечается profile_changed. Это "
        "только фиксация факта: задания и статус сессии не меняются"
    ),
    responses={
        404: {"description": "Сессия не найдена"},
        422: {"description": "Нужно ровно одно поле: profile или profile_hash"},
    },
    dependencies=[Depends(require(Role.CLIENT))],
)
async def check_profile(
    session_id: uuid.UUID, body: ProfileCheck, request: Request
) -> ProfileCheckOut:
    current = body.profile_hash or profile_hash(body.profile)
    try:
        changed = await service.check_profile(_generator(request), session_id, current)
    except service.NotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    return ProfileCheckOut(profile_changed=changed)


@router.get(
    "/sessions/{session_id}/selection",
    response_model=SelectionOut,
    summary="Объяснение отбора заданий",
    description=(
        "Для работодателя: как отбирались шаблоны — режим сходства, параметры, расстояния, "
        "вероятности и замены шаблонов, которые не дали экземпляра. Тела шаблонов, эталоны "
        "и тесты не выдаются"
    ),
    responses={
        404: {"description": "Сессия не найдена"},
        409: {"description": "Сессия создана не генерацией, объяснения отбора нет"},
    },
    dependencies=[Depends(require(Role.CLIENT))],
)
async def get_selection(session_id: uuid.UUID, request: Request) -> SelectionOut:
    try:
        view = await service.get_session(_generator(request), session_id)
    except service.NotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    selection = view.session.selection
    if not selection or service.SEED_KEY not in selection:
        raise HTTPException(status.HTTP_409_CONFLICT, "У сессии нет объяснения отбора")
    return SelectionOut(
        session_id=session_id,
        seed=selection[service.SEED_KEY],
        selection_mode=selection["selection_mode"],
        fallback_reason=selection["fallback_reason"],
        parameters=selection["parameters"],
        candidates=selection["candidates"],
        selected=[SelectedOut.model_validate(item) for item in selection["selected"]],
        replacements=[
            ReplacementOut(position=task.position, **item)
            for task in view.tasks
            for item in task.replacements
        ],
    )


@router.get(
    "/tasks/{task_id}",
    response_model=TaskOut,
    summary="Задание",
    description=(
        "Статус задания, а у готового — условие и публичная часть: сигнатура, примеры, "
        "окружение, ожидаемое время"
    ),
    responses={404: {"description": "Задание не найдено"}},
    dependencies=[Depends(require(Role.CLIENT))],
)
async def get_task(task_id: uuid.UUID, request: Request) -> TaskOut:
    try:
        return TaskOut.of(await service.get_task(_generator(request), task_id))
    except service.NotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
