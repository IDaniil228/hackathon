import asyncio
import time
from collections import OrderedDict, deque
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import build_engine
from app.handlers import (
    assessment,
    assignments,
    auth,
    documents,
    email,
    fsp,
    hiring,
    matching,
    messaging,
    profiles,
)
from app.llm.llm import ProviderRouter
from app.operations import worker
from app.readiness import expected_head
from app.security import hasher
from app.settings import Settings, get_settings


def create_app(settings: Settings | None = None):
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app):
        if len(settings.jwt_secret) < 32:
            raise RuntimeError("JWT_SECRET должен содержать не менее 32 символов")
        engine = build_engine(settings)
        async with engine.connect() as connection:
            writable = await connection.scalar(
                text(
                    "SELECT coalesce(bool_or(has_table_privilege(current_user, format('%I.%I', schemaname, tablename), 'INSERT,UPDATE,DELETE,TRUNCATE')), false) FROM pg_tables WHERE schemaname = 'match'"
                )
            )
        if writable:
            await engine.dispose()
            raise RuntimeError(
                "Runtime-роль API должна только читать match; примените api/db-init.sql и смените DATABASE_URL"
            )
        app.state.engine = engine
        app.state.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        app.state.http = httpx.AsyncClient(
            timeout=settings.service_timeout_seconds, follow_redirects=False
        )
        app.state.dummy_password_hash = await asyncio.to_thread(hasher.hash, "not-a-user-password")
        app.state.pdf_limit = asyncio.Semaphore(2)
        app.state.auth_attempts = OrderedDict()
        app.state.resume_llm = (
            ProviderRouter.from_settings(settings.llm) if settings.resume_llm_enabled else None
        )
        task = asyncio.create_task(worker(app))
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await app.state.http.aclose()
            if app.state.resume_llm is not None:
                await app.state.resume_llm.aclose()
            await engine.dispose()

    app = FastAPI(title="Платформа ФСП", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "Idempotency-Key"],
    )

    @app.exception_handler(IntegrityError)
    async def integrity_error(request: Request, error: IntegrityError):
        return JSONResponse(
            status_code=409, content={"detail": "Данные конфликтуют с ограничениями хранилища"}
        )

    @app.exception_handler(httpx.RequestError)
    async def upstream_unavailable(request: Request, error: httpx.RequestError):
        return JSONResponse(status_code=502, content={"detail": "Внешний сервис недоступен"})

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, error: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {key: item[key] for key in ("loc", "msg", "type")} for item in error.errors()
                ]
            },
        )

    @app.middleware("http")
    async def authentication_limit(request: Request, call_next):
        if request.url.path in ("/auth/register", "/auth/login", "/auth/password/request"):
            attempts = request.app.state.auth_attempts
            host = request.client.host if request.client else "unknown"
            timestamp = time.monotonic()
            queue = attempts.setdefault(host, deque())
            attempts.move_to_end(host)
            while queue and queue[0] < timestamp - 60:
                queue.popleft()
            if len(queue) >= settings.auth_requests_per_minute:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Слишком много запросов"},
                    headers={"Retry-After": "60"},
                )
            queue.append(timestamp)
            if len(attempts) > 10000:
                attempts.popitem(last=False)
        response = await call_next(request)
        if request.headers.get("authorization") or request.url.path.startswith("/auth/"):
            response.headers["Cache-Control"] = "private, no-store"
        return response

    @app.get("/health", tags=["Состояние"], summary="Готовность API и БД")
    async def health(request: Request):
        try:
            async with request.app.state.sessionmaker() as session:
                await session.execute(text("SELECT id FROM core.users LIMIT 1"))
                revision = await session.scalar(
                    text("SELECT version_num FROM public.alembic_version")
                )
                if revision != expected_head():
                    return JSONResponse(
                        status_code=503, content={"detail": "Миграции БД не соответствуют API"}
                    )
        except SQLAlchemyError:
            return JSONResponse(status_code=503, content={"detail": "БД или миграции недоступны"})
        return {"status": "ok"}

    for router in (
        auth.router,
        profiles.router,
        messaging.router,
        hiring.router,
        matching.router,
        assessment.router,
        documents.router,
        fsp.router,
        assignments.router,
        email.router,
    ):
        app.include_router(router)
    return app


app = create_app()
