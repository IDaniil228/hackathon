"""Сборка приложения: настройки, БД, эмбеддер, клиент LLM, источник summary и роутеры."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from matcher import auth
from matcher.api import admin_router, spawn
from matcher.api import router as runs_router
from matcher.config import Settings, get_settings
from matcher.db import make_engine, make_sessionmaker
from matcher.embeddings import DimensionMismatchError, check_dimension
from matcher.llm.contracts import Embedder, LLMClient
from matcher.llm.llm import ProviderRouter
from matcher.llm_stage import llm_stages
from matcher.runs import RunService
from matcher.stages import RunStages
from matcher.summaries import SummarySource, TaskgenSummaries

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    llm: LLMClient | None = None,
    embedder: Embedder | None = None,
    summaries: SummarySource | None = None,
    stages: RunStages | None = None,
) -> FastAPI:
    """llm, embedder, summaries и stages заменяют клиентов из настроек ещё до старта.

    Это нужно тестам и встраиванию: проверка размерности вектора и подхват незавершённых
    прогонов идут при старте, раньше, чем state можно подменить снаружи. llm нужен LLM-стадии
    при llm_stage.enabled; stages заменяют её целиком.
    """
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        sessionmaker = make_sessionmaker(engine)
        wants_llm = stages is None and settings.llm_stage.enabled and llm is None
        # Без LLM-стадии нужна только роль embedding: ключ облачного провайдера не нужен.
        own_router = (
            ProviderRouter.from_settings(settings.llm, roles=None if wants_llm else {"embedding"})
            if embedder is None or wants_llm
            else None
        )
        own_summaries = (
            TaskgenSummaries(settings.taskgen)
            if summaries is None and settings.taskgen.base_url
            else None
        )
        active_embedder = embedder or own_router
        active_stages = stages or (
            llm_stages(llm or own_router, settings) if settings.llm_stage.enabled else RunStages()
        )
        await _check_dimension(
            sessionmaker, active_embedder, settings.embeddings.startup_check_seconds
        )

        app.state.settings = settings
        app.state.sessionmaker = sessionmaker
        app.state.run_tasks = set()
        service = RunService(
            sessionmaker,
            settings,
            embedder=active_embedder,
            summaries=summaries or own_summaries,
            stages=active_stages,
        )
        app.state.run_service = service
        for run_id in await service.pending_runs():
            logger.info("Прогон %s остался незавершённым, исполняется заново", run_id)
            spawn(app, service.execute(run_id))
        yield
        for task in list(app.state.run_tasks):
            task.cancel()
        await asyncio.gather(*app.state.run_tasks, return_exceptions=True)
        if own_summaries is not None:
            await own_summaries.aclose()
        if own_router is not None:
            await own_router.aclose()
        await engine.dispose()

    app = FastAPI(
        title="matcher",
        summary="Подбор кандидатов под потребность работодателя",
        lifespan=lifespan,
    )
    app.include_router(auth.router)
    app.include_router(runs_router)
    app.include_router(admin_router)

    @app.get("/health", tags=["Служебное"], summary="Проверка, что сервис запущен")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


async def _check_dimension(sessionmaker, embedder: Embedder | None, timeout: float) -> None:
    """Неверная размерность останавливает старт; недоступная модель — только предупреждение.

    Без модели сервис отвечает, а прогоны с векторной стадией завершаются ошибкой.
    """
    if embedder is None:
        return
    try:
        async with sessionmaker() as session:
            await asyncio.wait_for(check_dimension(session, embedder), timeout)
    except DimensionMismatchError:
        raise
    except Exception as error:
        logger.warning("Размерность вектора не проверена, эмбеддер недоступен: %r", error)
