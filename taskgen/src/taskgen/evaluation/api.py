"""HTTP API оценки. Логика — в service.py, здесь только перевод в HTTP.

Контракт скрытых данных задания (tasks.hidden_payload) описан в docstring service.py.
Песочница берётся из app.state.sandbox, детектор ИИ — из app.state.ai_detector.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from taskgen.auth import AUTH_ERRORS, Role, require
from taskgen.evaluation import service
from taskgen.evaluation.service import Behavior, Evaluator, SessionResult, Submission

router = APIRouter(
    tags=["Оценка"], dependencies=[Depends(require(Role.CLIENT))], responses=AUTH_ERRORS
)

_STATUS = {
    service.NotFoundError: status.HTTP_404_NOT_FOUND,
    service.ConflictError: status.HTTP_409_CONFLICT,
    service.InvalidAnswerError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    service.SandboxUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    service.JudgeUnavailableError: status.HTTP_502_BAD_GATEWAY,
    service.BrokenTaskError: status.HTTP_500_INTERNAL_SERVER_ERROR,
}


def _http(error: service.EvaluationError) -> HTTPException:
    return HTTPException(_STATUS[type(error)], str(error))


def _evaluator(request: Request) -> Evaluator:
    return Evaluator.from_state(request.app.state)


class AnswerSubmit(BaseModel):
    content: str | None = Field(
        default=None,
        min_length=1,
        max_length=200_000,
        description="Текст ответа или код; для тестовой задачки не передаётся",
    )
    choice: str | None = Field(
        default=None,
        min_length=1,
        max_length=16,
        description="Только для тестовой задачки: id выбранного варианта из public.options",
    )
    language: str | None = Field(
        default=None,
        description="Язык ответа для задания с кодом; по умолчанию — язык задания",
    )
    paste_count: int | None = Field(default=None, ge=0, description="Число вставок из буфера")
    focus_lost_count: int | None = Field(
        default=None, ge=0, description="Сколько раз кандидат уходил со страницы теста"
    )
    time_spent_sec: float | None = Field(
        default=None, ge=0, description="Время работы над заданием, секунды"
    )


class AnswerAccepted(BaseModel):
    answer_id: uuid.UUID
    task_id: uuid.UUID
    status: str = Field(default="accepted", description="Ответ сохранён и будет оценён")


class SummaryOut(BaseModel):
    candidate_ref: str
    text: str = Field(description="Текст для рекрутера; каждое утверждение опирается на data")
    data: dict[str, Any] = Field(description="Факты из оценённых сессий кандидата")
    updated_at: datetime


@router.post(
    "/tasks/{task_id}/answer",
    response_model=AnswerAccepted,
    summary="Отправить ответ на задание",
    description=(
        "Сохраняет ответ кандидата и поведенческие сигналы фронтенда. Код сразу прогоняется "
        "на скрытых тестах в песочнице и статическими анализаторами; результат прогона "
        "в ответе не раскрывается. На тестовую задачку отвечают полем choice — id варианта, "
        "на остальные задания — полем content. Ответ принимается, только когда сессия выдаёт "
        "задания (tasks_issued в GET /sessions/{id}). Повторная отправка до оценки сессии "
        "заменяет ответ"
    ),
    responses={
        404: {"description": "Задание не найдено"},
        409: {
            "description": "Сессия уже оценена, ещё генерируется без раннего старта или её "
            "генерация не удалась, либо задание ещё не готово; detail объясняет, что именно"
        },
        422: {
            "description": "Язык ответа не совпадает с языком задания, вариант задачки "
            "неизвестен или вместо choice передан content и наоборот"
        },
        500: {"description": "Скрытые данные задания повреждены"},
        503: {"description": "Песочница не настроена или недоступна"},
    },
)
async def submit_answer(task_id: uuid.UUID, body: AnswerSubmit, request: Request) -> AnswerAccepted:
    submission = Submission(
        content=body.content,
        choice=body.choice,
        language=body.language,
        behavior=Behavior(
            paste_count=body.paste_count,
            focus_lost_count=body.focus_lost_count,
            time_spent_sec=body.time_spent_sec,
        ),
    )
    try:
        answer_id = await service.submit_answer(_evaluator(request), task_id, submission)
    except service.EvaluationError as error:
        raise _http(error) from error
    return AnswerAccepted(answer_id=answer_id, task_id=task_id)


@router.post(
    "/sessions/{session_id}/score",
    response_model=SessionResult,
    summary="Оценить сессию",
    description=(
        "Оценивает все задания сессии: тесты, судья по рубрике, качество кода и скорость. "
        "Присваивает грейд по уровням, записывает результат и обновляет summary кандидата. "
        "Повторный вызов пересчитывает результат"
    ),
    responses={
        404: {"description": "Сессия не найдена"},
        409: {
            "description": "Сессия ещё генерируется, её генерация не удалась или в ней нет "
            "заданий; detail объясняет, что именно"
        },
        500: {"description": "Скрытые данные задания повреждены"},
        502: {"description": "Судья недоступен, сессия не оценена"},
    },
)
async def score_session(session_id: uuid.UUID, request: Request) -> SessionResult:
    try:
        return await service.score_session(_evaluator(request), session_id)
    except service.EvaluationError as error:
        raise _http(error) from error


@router.get(
    "/sessions/{session_id}/result",
    response_model=SessionResult,
    summary="Результат оценки сессии",
    description="Результат последней оценки сессии без скрытых данных заданий",
    responses={
        404: {"description": "Сессия не найдена"},
        409: {"description": "Сессия ещё не оценена"},
    },
)
async def get_result(session_id: uuid.UUID, request: Request) -> SessionResult:
    try:
        return await service.session_result(_evaluator(request), session_id)
    except service.EvaluationError as error:
        raise _http(error) from error


@router.get(
    "/candidates/{candidate_ref}/summary",
    response_model=SummaryOut,
    summary="Machine summary кандидата",
    description=(
        "Сводка по всем оценённым сессиям кандидата для индексации и показа рекрутеру. "
        "Её пишет только сервис после оценки сессии; изменить её через API нельзя"
    ),
    responses={404: {"description": "У кандидата нет оценённых сессий"}},
)
async def get_summary(candidate_ref: str, request: Request) -> SummaryOut:
    try:
        summary = await service.candidate_summary(_evaluator(request), candidate_ref)
    except service.EvaluationError as error:
        raise _http(error) from error
    return SummaryOut(
        candidate_ref=summary.candidate_ref,
        text=summary.text,
        data=summary.data,
        updated_at=summary.updated_at,
    )
