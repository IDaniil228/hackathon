"""Асинхронный движок и сессия для FastAPI."""

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)

from app.settings import get_settings


def build_engine(settings=None) -> AsyncEngine:
    settings = settings or get_settings()
    return create_async_engine(
        settings.async_database_url,
        echo=settings.echo_sql,
        hide_parameters=True,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Обработчик фиксирует изменения до формирования успешного ответа"""
    async with request.app.state.sessionmaker() as session:
        try:
            yield session
        finally:
            await session.rollback()
