import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine

from taskgen.config import Settings
from taskgen.db import CONNECT_ARGS


class FakeEmbedder:
    """Эмбеддер с заданными векторами: текст → вектор; неизвестный текст — ошибка теста."""

    def __init__(self, vectors: dict[str, list[float]] | None = None, *, model: str = "fake"):
        self.model_name = model
        self.vectors = dict(vectors or {})
        self.calls: list[list[str]] = []
        self.fail: Exception | None = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.fail is not None:
            raise self.fail
        return [self.vectors.get(text, [1.0, 0.0, 0.0]) for text in texts]


@pytest.fixture
def unique() -> str:
    """Префикс имён шаблонов: тесты не сталкиваются с данными в общей БД."""
    return f"t{uuid.uuid4().hex[:10]}"


@pytest.fixture
async def db_connection(settings: Settings) -> AsyncIterator[AsyncConnection]:
    """Соединение во внешней транзакции, которая откатывается после теста."""
    engine = create_async_engine(settings.database_url, connect_args=CONNECT_ARGS)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            await transaction.rollback()
    await engine.dispose()


@pytest.fixture
async def db_session(db_connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    # create_savepoint превращает commit внутри кода в точку сохранения внешней транзакции.
    session = AsyncSession(
        bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    try:
        yield session
    finally:
        await session.close()
