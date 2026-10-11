"""Эндпоинты прогонов подбора. Вызывает их api ключом роли client; пересчёт векторов — admin."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Coroutine
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from matcher import geometry, repository
from matcher.auth import AUTH_ERRORS, Role, require
from matcher.db import MatchStage, RunRequest, RunStatus
from matcher.runs import (
    ChatAuthorMissingError,
    NeedInactiveError,
    NeedNotFoundError,
    RequestConflictError,
    RunNotFinishedError,
    RunNotFoundError,
    RunService,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Подбор"], dependencies=[Depends(require(Role.CLIENT))], responses=AUTH_ERRORS
)
admin_router = APIRouter(
    tags=["Служебное"], dependencies=[Depends(require(Role.ADMIN))], responses=AUTH_ERRORS
)


def spawn(app: FastAPI, work: Coroutine[Any, Any, Any]) -> asyncio.Task:
    """Фоновая задача приложения; при остановке приложение отменяет её и ждёт."""
    # Ссылка на задачу держит её до конца: иначе цикл событий может собрать её сборщиком.
    tasks: set[asyncio.Task] = app.state.run_tasks
    task = asyncio.create_task(work)
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return task


class RunCreate(BaseModel):
    request_ref: uuid.UUID | None = Field(
        default=None,
        description="Идентификатор операции api; одинаковый запрос возвращает прежний прогон",
    )
    author_user_id: uuid.UUID | None = Field(
        default=None, description="Пользователь api, который запустил подбор"
    )


class RunAccepted(BaseModel):
    run_id: uuid.UUID
    status: RunStatus


class RequestRunOut(BaseModel):
    run_id: uuid.UUID


class ResultOut(BaseModel):
    candidate_id: uuid.UUID
    rank: int
    stage: MatchStage
    vector_score: float | None
    llm_score: float | None
    final_score: float | None
    explanation: str | None
    factors: dict[str, Any] | None = Field(
        description="grade_confirmed, factor_score и items: у каждого фактора value 0..1, "
        "line — строка объяснения, applicable — применим ли фактор к запросу, numbers — числа "
        "строки. llm — попадания в негативные промты от ранжировщика, если он оценил кандидата"
    )


class ResultBrief(BaseModel):
    candidate_id: uuid.UUID
    rank: int
    final_score: float | None


class RunOut(BaseModel):
    id: uuid.UUID
    need_id: uuid.UUID
    parent_run_id: uuid.UUID | None
    status: RunStatus
    strategy: str
    params: dict[str, Any] | None
    pool_size: int | None
    llm_model: str | None = Field(description="Модель ранжировщика; null — порядок по факторам")
    summary: str | None = Field(description="Обзор подборки для работодателя")
    error_message: str | None
    created_at: datetime
    finished_at: datetime | None
    results: list[ResultOut] = Field(description="Ранжированная выдача, по возрастанию rank")


class ResultsOut(BaseModel):
    total: int = Field(description="Сколько строк в выдаче прогона")
    matched: int = Field(description="Сколько из них прошли фильтры")
    results: list[ResultOut] | list[ResultBrief] = Field(
        description="Строки, которые прошли фильтры, по возрастанию rank"
    )


class RefineIn(BaseModel):
    request_ref: uuid.UUID | None = Field(
        default=None,
        description="Идентификатор операции api, защищает от повторного создания прогона и реплик",
    )
    message: str = Field(min_length=1, max_length=20000, description="Уточнение работодателя")
    author_user_id: uuid.UUID | None = Field(
        default=None,
        description="Пользователь api, который уточняет подбор; без него тред чата, если его "
        "нет, создаётся от автора потребности",
    )


class MapVisible(BaseModel):
    candidate_ids: list[uuid.UUID] = Field(max_length=10000)


class MapForget(BaseModel):
    before: datetime


@router.post(
    "/candidates/{candidate_id}/maps/revoke",
    summary="Очистить прежние векторы после отзыва обработки",
)
async def forget_maps(candidate_id: uuid.UUID, body: MapForget, request: Request):
    if body.before.tzinfo is None:
        raise HTTPException(422, "Нужен момент с часовым поясом")
    async with request.app.state.sessionmaker() as session, session.begin():
        await geometry.forget_before(session, candidate_id, body.before)
    return {"status": "done"}


@router.get("/runs/{run_id}/map-members", summary="Источники точек для проверки прав основным API")
async def map_members(run_id: uuid.UUID, request: Request):
    async with request.app.state.sessionmaker() as session:
        await _load_run(session, run_id)
        payload = await geometry.snapshot(session, run_id)
    if payload is None:
        return {"state": "unavailable", "members": []}
    return {
        "state": "ready",
        "members": [
            {"candidate_id": key, "requires": value["requires"]}
            for key, value in payload["candidates"].items()
        ],
    }


@router.post(
    "/runs/{run_id}/map", summary="Геометрия разрешённого подмножества без выдачи векторов"
)
async def run_map(run_id: uuid.UUID, body: MapVisible, request: Request):
    async with request.app.state.sessionmaker() as session:
        run = await _load_run(session, run_id)
        payload = await geometry.snapshot(session, run_id)
        if payload is None:
            return {"state": "unavailable", "points": []}
        current = await geometry.revisions(session, body.candidate_ids)
        vectors = {
            str(key): payload["candidates"][str(key)]["vector"]
            for key in body.candidate_ids
            if str(key) in payload["candidates"]
            and current.get(str(key)) == payload["candidates"][str(key)]["revision"]
        }
        ranks = dict(
            (
                await session.execute(
                    text(
                        "SELECT candidate_id, rank FROM match.search_results "
                        "WHERE run_id=:id AND rank IS NOT NULL"
                    ),
                    {"id": run_id},
                )
            ).all()
        )
    result = await asyncio.to_thread(geometry.project_vectors, vectors, payload["query"])
    for point in result["points"]:
        key = point["candidate_id"]
        point["rank"] = ranks.get(uuid.UUID(key)) if run["status"] == "done" else None
        point["exploration"] = (
            "fresh"
            if key in payload["exploration"]["fresh"]
            else "random"
            if key in payload["exploration"]["random"]
            else None
        )
    return {**result, "state": "ready", "phase": "final" if run["status"] == "done" else "preview"}


def _service(request: Request) -> RunService:
    return request.app.state.run_service


@router.post(
    "/needs/{need_id}/runs",
    response_model=RunAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Запустить подбор по потребности",
    description="Создаёт прогон со статусом queued и исполняет его в фоне; статус и выдача — "
    "GET /runs/{run_id}",
    responses={
        404: {"description": "Потребности нет"},
        409: {"description": "Потребность закрыта"},
    },
)
async def start_run(
    need_id: uuid.UUID, request: Request, body: RunCreate | None = None
) -> RunAccepted:
    service = _service(request)
    try:
        run_id, created = await service.create_run_once(
            need_id,
            author_user_id=body.author_user_id if body else None,
            request_ref=body.request_ref if body else None,
            client_hash=request.state.client_hash,
        )
    except NeedNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except NeedInactiveError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except RequestConflictError as error:
        raise HTTPException(409, str(error)) from error
    if created:
        spawn(request.app, service.execute(run_id))
    return RunAccepted(run_id=run_id, status=RunStatus.QUEUED)


_RUN = text(
    """
    SELECT id, need_id, parent_run_id, status::text AS status, strategy, params, pool_size,
           llm_model, summary, error_message, created_at, finished_at
    FROM match.search_runs WHERE id = :id
    """
)


async def _load_run(session, run_id: uuid.UUID) -> dict[str, Any]:
    run = (await session.execute(_RUN, {"id": run_id})).mappings().first()
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"прогона {run_id} нет")
    return dict(run)


@router.get(
    "/runs/{run_id}",
    response_model=RunOut,
    summary="Статус прогона и выдача",
    responses={404: {"description": "Прогона нет"}},
)
async def get_run(run_id: uuid.UUID, request: Request) -> RunOut:
    async with request.app.state.sessionmaker() as session:
        run = await _load_run(session, run_id)
        _, rows = await repository.run_results(session, run_id, repository.ResultFilters())
    return RunOut(**run, results=[ResultOut(**row) for row in rows])


@router.get(
    "/runs/{run_id}/results",
    response_model=ResultsOut,
    summary="Выдача готового прогона с фильтрами",
    description="Сужает выдачу готового прогона без нового подбора и ничего не пишет. Все "
    "фильтры необязательны и действуют вместе",
    responses={
        404: {"description": "Прогона нет"},
        409: {"description": "Прогон ещё не завершён"},
    },
)
async def get_results(
    run_id: uuid.UUID,
    request: Request,
    city: str | None = Query(default=None, description="Город кандидата, без учёта регистра"),
    relocation: bool | None = Query(default=None, description="Готовность к переезду"),
    work_format: Literal["office", "hybrid", "remote"] | None = Query(
        default=None,
        description="Предпочтительный формат работы; кандидат без предпочтения проходит",
    ),
    grade_confirmed: bool | None = Query(default=None, description="Грейд подтверждён тестом"),
    skills: Annotated[
        list[str],
        Query(description="Коды навыков ref.skills.code; нужны все, уровень не важен"),
    ] = (),
    salary_max: int | None = Query(
        default=None,
        ge=0,
        description="Бюджет: нижняя граница ожиданий кандидата не выше; ожидания в другой "
        "валюте или без ожиданий проходят",
    ),
    min_score: float | None = Query(
        default=None, ge=0.0, le=1.0, description="Нижняя граница final_score"
    ),
    view: Literal["full", "brief"] = Query(
        default="full", description="brief — только candidate_id, rank и final_score"
    ),
) -> ResultsOut:
    filters = repository.ResultFilters(
        city=city,
        relocation=relocation,
        work_format=work_format,
        grade_confirmed=grade_confirmed,
        skills=tuple(skills),
        salary_max=salary_max,
        min_score=min_score,
    )
    async with request.app.state.sessionmaker() as session:
        run = await _load_run(session, run_id)
        if run["status"] != RunStatus.DONE:
            detail = f"прогон {run_id} в статусе {run['status']}"
            raise HTTPException(status.HTTP_409_CONFLICT, detail)
        total, rows = await repository.run_results(session, run_id, filters)
    model = ResultOut if view == "full" else ResultBrief
    results = [model(**{name: row[name] for name in model.model_fields}) for row in rows]
    return ResultsOut(total=total, matched=len(rows), results=results)


@router.post(
    "/runs/{run_id}/refine",
    response_model=RunAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Уточнить подбор сообщением работодателя",
    description="Создаёт новый прогон с parent_run_id: переписчик запроса видит потребность и "
    "все уточнения цепочки. В тред чата потребности пишутся реплика работодателя и ответ "
    "сервиса с id нового прогона. Прежняя выдача не меняется",
    responses={
        404: {"description": "Прогона нет"},
        409: {"description": "Прогон не завершён или потребность закрыта"},
        422: {"description": "У потребности нет треда чата и не известен его автор"},
    },
)
async def refine_run(run_id: uuid.UUID, body: RefineIn, request: Request) -> RunAccepted:
    service = _service(request)
    try:
        new_id, created = await service.refine_once(
            run_id,
            body.message,
            author_user_id=body.author_user_id,
            request_ref=body.request_ref,
            client_hash=request.state.client_hash,
        )
    except RunNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except (RunNotFinishedError, NeedInactiveError) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except ChatAuthorMissingError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    except RequestConflictError as error:
        raise HTTPException(409, str(error)) from error
    if created:
        spawn(request.app, service.execute(new_id))
    return RunAccepted(run_id=new_id, status=RunStatus.QUEUED)


@router.get(
    "/requests/{request_ref}/run",
    response_model=RequestRunOut,
    summary="Найти прогон по операции текущего клиента",
    responses={404: {"description": "Запрос текущего клиента не найден"}},
)
async def request_run(request_ref: uuid.UUID, request: Request):
    async with request.app.state.sessionmaker() as session:
        row = await session.get(RunRequest, (request.state.client_hash, request_ref))
        if row is None:
            raise HTTPException(404, "Запрос клиента не найден")
        return {"run_id": row.run_id}


class RefreshAccepted(BaseModel):
    status: Literal["accepted"] = "accepted"


async def _refresh(service: RunService) -> None:
    try:
        total, written = await service.refresh_embeddings()
    except Exception:
        logger.exception("Фоновый пересчёт векторов не удался")
        return
    logger.info("Фоновый пересчёт векторов: кандидатов %d, записано %d", total, written)


@admin_router.post(
    "/embeddings/refresh",
    response_model=RefreshAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Пересчитать устаревшие векторы карточек в фоне",
    description="Досчитывает векторы кандидатов, карточки которых изменились с прошлого "
    "пересчёта; то же делает команда matcher embed",
    responses={
        409: {"description": "Пересчёт уже идёт"},
        503: {"description": "Эмбеддер не настроен"},
    },
)
async def refresh_embeddings(request: Request) -> RefreshAccepted:
    service = _service(request)
    if service.embedder is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "эмбеддер не настроен")
    running: asyncio.Task | None = getattr(request.app.state, "refresh_task", None)
    if running is not None and not running.done():
        raise HTTPException(status.HTTP_409_CONFLICT, "пересчёт векторов уже идёт")
    request.app.state.refresh_task = spawn(request.app, _refresh(service))
    return RefreshAccepted()
