"""Интеграционные тесты идут на своей базе api под ролью matcher.

Без своей базы тесты пошли бы в адрес по умолчанию и упали бы ложно, как тесты taskgen
(память проекта taskgen-test-db). Поэтому адрес не берётся по умолчанию, а обязателен:

    docker exec <контейнер postgres> createdb -U <владелец> <имя>
    из api/: DATABASE_URL=... uv run alembic upgrade head, uv run fspdb seed,
             uv run fspdb seed-demo, uv run fspdb relevance
    psql -v role=matcher -v password=matcher -v db=<имя> -v owner=<владелец> -f db-init.sql
    из matcher/: MATCHER_DATABASE_URL=... uv run alembic upgrade head

MATCHER_DATABASE_URL — адрес под ролью matcher, MATCHER_TEST_OWNER_URL — под владельцем
таблиц api: им тесты готовят данные в транзакции, которая откатывается.
"""

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from matcher.db import make_engine

_HERE = Path(__file__).parent

DEMO_NEED = "88888888-0000-4000-8000-000000000001"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if _HERE in Path(item.fspath).parents:
            item.add_marker(pytest.mark.integration)


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(
            f"Не задан {name}: интеграционным тестам нужна своя база api с ролью matcher, "
            "порядок подготовки — в tests/integration/conftest.py. Юнит-тесты без базы: "
            'pytest -m "not integration"',
            pytrace=False,
        )
    return value


async def purge_embeddings(session: AsyncSession | AsyncConnection, model: str) -> None:
    """Удаляет векторы модели, которые тест зафиксировал в базе."""
    for table in ("match.candidate_embeddings", "match.need_embeddings"):
        await session.execute(text(f"DELETE FROM {table} WHERE model = :m"), {"m": model})


@pytest.fixture
def database_url() -> str:
    return _required("MATCHER_DATABASE_URL")


@pytest.fixture
def owner_url() -> str:
    return _required("MATCHER_TEST_OWNER_URL")


async def _rolled_back(url: str) -> AsyncIterator[AsyncConnection]:
    engine = make_engine(url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            await transaction.rollback()
    await engine.dispose()


@pytest.fixture
async def matcher_session(database_url: str) -> AsyncIterator[AsyncSession]:
    """Сессия роли matcher во внешней транзакции, которая откатывается после теста."""
    async for connection in _rolled_back(database_url):
        session = AsyncSession(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        yield session
        await session.close()


@pytest.fixture
async def owner_session(owner_url: str) -> AsyncIterator[AsyncSession]:
    """Сессия владельца таблиц api в откатываемой транзакции: подготовка данных."""
    async for connection in _rolled_back(owner_url):
        session = AsyncSession(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        yield session
        await session.close()
