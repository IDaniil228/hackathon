"""Асинхронный движок и сессия для FastAPI."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.settings import get_settings


def build_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.async_database_url,
        echo=settings.echo_sql,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )


engine: AsyncEngine = build_engine()

SessionFactory = async_sessionmaker(
    engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Зависимость FastAPI: сессия на запрос, коммит на выходе без ошибок."""
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
