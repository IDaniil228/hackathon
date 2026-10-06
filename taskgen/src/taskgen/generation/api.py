"""HTTP API тестовых сессий. Логика — в service.py, здесь только перевод в HTTP.

Наружу уходит только публичная часть заданий: hidden_payload не попадает ни в один ответ,
потому что модели ответов его не содержат.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from taskgen.auth import Role, require
from taskgen.contracts import TaskKind
from taskgen.db import Task
from taskgen.generation import service
from taskgen.generation.instance import (
    LLMUnavailableError,
    ModelOutputError,
    PublicPayload,
    SandboxUnavailableError,
    TemplateDefectError,
)
from taskgen.generation.service import Generator, SessionSpec

router = APIRouter(tags=["Тестовые сессии"])

_FAILURE_STATUS = {
    SandboxUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    LLMUnavailableError: status.HTTP_502_BAD_GATEWAY,
    ModelOutputError: status.HTTP_502_BAD_GATEWAY,
    TemplateDefectError: status.HTTP_500_INTERNAL_SERVER_ERROR,
}

_AUTH_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"description": "Нет ключа или ключ отозван"},
    403: {"description": "Ключ не имеет нужной роли"},
}


def _generator(request: Request) -> Generator:
    return Generator.from_state(request.app.state)


# --- Модели ---------------------------------------------------------------------------------


class SelectionOverride(BaseModel):
    """Параметры отбора вместо test_configs и настроек; пустое поле не переопределяется."""

    model_config = ConfigDict(extra="forbid")

    temperature: float | None = Field(default=None, description="Температура отбора, 0..1")
    alpha: float | None = Field(default=None, description="Вес разницы грейдов")
    beta: float | None = Field(default=None, description="Вес несходства навыков")
    tasks_per_test: int | None = Field(default=None, description="Число заданий в тесте")


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


class TaskOut(BaseModel):
    """Задание глазами кандидата: условие и публичная часть, без скрытых тестов и рубрики."""

    id: uuid.UUID
    session_id: uuid.UUID
    position: int = Field(description="Номер задания в сессии, с 1")
    kind: TaskKind
    statement: str = Field(description="Текст условия в Markdown")
    public: PublicPayload

    @classmethod
    def of(cls, task: Task) -> TaskOut:
        return cls(
            id=task.id,
            session_id=task.session_id,
            position=task.position,
            kind=TaskKind(task.kind),
            statement=task.statement,
            public=PublicPayload.model_validate(task.public_payload),
        )


class SessionOut(BaseModel):
    id: uuid.UUID
    candidate_ref: str
    area: str
    claimed_grade: str
    skills: list[str]
    status: str = Field(
        description="generating — задания создаются, ready — выданы, failed — не созданы, "
        "scored — сессия оценена"
    )
    failure: str | None = Field(description="Причина статуса failed")
    assigned_grade: str | None = Field(description="Грейд по итогам оценки; null — до оценки")
    created_at: datetime
    tasks: list[TaskOut]

    @classmethod
    def of(cls, view: service.SessionView) -> SessionOut:
        session = view.session
        selection = session.selection or {}
        result = session.result or {}
        return cls(
            id=session.id,
            candidate_ref=session.candidate_ref,
            area=session.area_code,
            claimed_grade=session.claimed_grade_code,
            skills=list(session.skills),
            status=session.status,
            failure=selection.get(service.FAILURE_KEY),
            assigned_grade=result.get("assigned_grade"),
            created_at=session.created_at,
            tasks=[TaskOut.of(task) for task in view.tasks],
        )


class SelectedOut(BaseModel):
    template_id: uuid.UUID
    name: str
    version: int
    grade: str
    grade_gap: int = Field(description="Разность позиций грейда шаблона и заявленного")
    similarity: float = Field(description="Сходство шаблона с навыками кандидата")
    distance: float
    probability: float = Field(description="Вероятность выпасть первым из всего пула")
    step_probability: float = Field(description="Вероятность на том шаге, где шаблон выпал")


class SelectionOut(BaseModel):
    session_id: uuid.UUID
    seed: int
    selection_mode: str = Field(description="embeddings или skills_jaccard")
    fallback_reason: str | None = Field(description="Почему отбор шёл без эмбеддингов")
    parameters: dict[str, Any] = Field(description="Параметры отбора, с которыми он шёл")
    candidates: int = Field(description="Сколько шаблонов области участвовало в отборе")
    selected: list[SelectedOut] = Field(description="Выбранные шаблоны в порядке заданий")


# --- Эндпоинты ------------------------------------------------------------------------------


@router.post(
    "/sessions",
    response_model=SessionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать тестовую сессию",
    description=(
        "Отбирает шаблоны области по заявленному грейду и навыкам кандидата и генерирует по "
        "ним задания. Задание с кодом выдаётся только после того, как эталон прошёл все его "
        "тесты в песочнице. Параметры отбора берутся из настроек теста; переопределить их "
        "может только admin. Если задания создать не удалось, сессия остаётся со статусом "
        "failed, а detail ответа содержит message и session_id"
    ),
    responses={
        **_AUTH_ERRORS,
        409: {"description": "В области нет шаблонов"},
        422: {"description": "Неизвестная область, грейд или навык; неверные параметры отбора"},
        500: {"description": "Шаблон не даёт проверенного экземпляра; сессия failed"},
        502: {"description": "Модель генерации недоступна или не написала условие"},
        503: {"description": "Песочница не настроена или недоступна; сессия failed"},
    },
)
async def create_session(
    body: SessionCreate,
    request: Request,
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
    )
    try:
        session_id = await service.create_session(gen, spec)
    except service.InvalidRequestError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    except service.ConflictError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except service.SessionFailedError as error:
        code = _FAILURE_STATUS.get(type(error.cause), status.HTTP_500_INTERNAL_SERVER_ERROR)
        detail = {"message": str(error.cause), "session_id": str(error.session_id)}
        raise HTTPException(code, detail) from error
    return SessionOut.of(await service.get_session(gen, session_id))


@router.get(
    "/sessions/{session_id}",
    response_model=SessionOut,
    summary="Тестовая сессия",
    description="Статус сессии и её задания: условие и публичная часть, без скрытых тестов",
    responses={**_AUTH_ERRORS, 404: {"description": "Сессия не найдена"}},
    dependencies=[Depends(require(Role.CLIENT))],
)
async def get_session(session_id: uuid.UUID, request: Request) -> SessionOut:
    try:
        return SessionOut.of(await service.get_session(_generator(request), session_id))
    except service.NotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error


@router.get(
    "/sessions/{session_id}/selection",
    response_model=SelectionOut,
    summary="Объяснение отбора заданий",
    description=(
        "Для работодателя: как отбирались шаблоны — режим сходства, параметры, расстояния и "
        "вероятности. Тела шаблонов, эталоны и тесты не выдаются"
    ),
    responses={
        **_AUTH_ERRORS,
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
    )


@router.get(
    "/tasks/{task_id}",
    response_model=TaskOut,
    summary="Задание",
    description="Условие и публичная часть задания: сигнатура, примеры, ожидаемое время",
    responses={**_AUTH_ERRORS, 404: {"description": "Задание не найдено"}},
    dependencies=[Depends(require(Role.CLIENT))],
)
async def get_task(task_id: uuid.UUID, request: Request) -> TaskOut:
    try:
        return TaskOut.of(await service.get_task(_generator(request), task_id))
    except service.NotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
