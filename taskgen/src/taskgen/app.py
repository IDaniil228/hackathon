"""Сборка приложения: настройки, БД, LLM, фоновая генерация и роутеры модулей."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from taskgen import auth
from taskgen.bank.api import router as bank_router
from taskgen.config import Settings, get_settings
from taskgen.contracts import LLMClient, SandboxRunner
from taskgen.db import make_sessionmaker
from taskgen.evaluation.api import router as evaluation_router
from taskgen.generation.api import router as generation_router
from taskgen.generation.worker import GenerationWorker
from taskgen.llm import OpenAICompatibleClient
from taskgen.sandbox.piston import PistonRunner


def create_app(
    settings: Settings | None = None,
    *,
    llm: LLMClient | None = None,
    sandbox: SandboxRunner | None = None,
) -> FastAPI:
    """llm и sandbox заменяют клиентов из настроек ещё до старта.

    Это нужно встраиванию и тестам, в которых фоновая генерация начинается при старте
    приложения, раньше, чем state можно подменить снаружи.
    """
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.sessionmaker = make_sessionmaker(settings.database_url)
        own_llm = OpenAICompatibleClient(settings.llm) if llm is None else None
        own_sandbox = PistonRunner(settings.sandbox) if sandbox is None else None
        app.state.llm = llm or own_llm
        app.state.sandbox = sandbox or own_sandbox
        worker = GenerationWorker(app.state)
        app.state.generation_worker = worker
        # Банк сообщает об импорте через state и не зависит от модуля генерации.
        app.state.on_templates_imported = worker.wake_pool
        if settings.generation.autostart:
            await worker.resume()
            worker.start_pool_loop()
        yield
        await worker.aclose()
        # Закрываются созданные здесь клиенты: тесты и встраивание подменяют state своими.
        if own_sandbox is not None:
            await own_sandbox.aclose()
        if own_llm is not None:
            await own_llm.aclose()

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
