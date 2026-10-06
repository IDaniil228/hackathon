"""Сборка приложения: настройки, БД, LLM и роутеры модулей."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from taskgen import auth
from taskgen.bank.api import router as bank_router
from taskgen.config import Settings, get_settings
from taskgen.db import make_sessionmaker
from taskgen.evaluation.api import router as evaluation_router
from taskgen.generation.api import router as generation_router
from taskgen.llm import OpenAICompatibleClient


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.sessionmaker = make_sessionmaker(settings.database_url)
        app.state.llm = OpenAICompatibleClient(settings.llm)
        yield
        await app.state.llm.aclose()

    app = FastAPI(
        title="taskgen",
        summary="Генерация и оценка заданий для теста на грейд",
        lifespan=lifespan,
    )
    app.include_router(auth.router)
    app.include_router(bank_router)
    app.include_router(generation_router)
    app.include_router(evaluation_router)

    @app.get("/health", tags=["Служебное"], summary="Проверка, что сервис запущен")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
