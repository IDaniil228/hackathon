"""Подключение к базе api и собственные таблицы сервиса в схеме matcher.

Таблицы ref, profile, assess, hire и match принадлежат api и описаны её моделями и
миграциями (api/app/db/models). Матчер обращается к ним SQL-запросами по именам колонок и не
держит вторую копию моделей: расхождение со схемой ловят интеграционные тесты.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, MetaData, String, func
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "matcher"


class RunStatus(StrEnum):
    """Значения типа match.run_status из api/app/db/enums.py; сверку держит интеграционный
    тест test_enums_match_database."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class MatchStage(StrEnum):
    """Значения типа match.match_stage: до какой стадии конвейера дошёл кандидат."""

    PREFILTER = "prefilter"
    VECTOR = "vector"
    LLM = "llm"


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# Схема по умолчанию — public, как в taskgen: иначе роль matcher со своей схемой matcher
# увидела бы её первой в search_path, а autogenerate получил бы объекты без схемы.
CONNECT_ARGS = {"server_settings": {"search_path": "public"}}


def make_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True, connect_args=CONNECT_ARGS)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
