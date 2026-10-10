"""Команда matcher embed: векторы всех кандидатов, которых можно найти."""

from sqlalchemy import text

from matcher.cli import embed_command
from matcher.config import Settings
from matcher.db import make_engine
from tests.conftest import FakeEmbedder


async def test_embed_command_fills_cache_once(database_url: str) -> None:
    embedder = FakeEmbedder(model="fake-cli-embedder")
    settings = Settings(database_url=database_url)
    engine = make_engine(database_url)
    try:
        assert await embed_command(settings, embedder=embedder) == 0
        async with engine.connect() as connection:
            stored = await connection.scalar(
                text("SELECT count(*) FROM match.candidate_embeddings WHERE model = :m"),
                {"m": embedder.model_name},
            )
            searchable = await connection.scalar(
                text(
                    "SELECT count(*) FROM profile.candidates c "
                    "LEFT JOIN profile.candidate_privacy p ON p.candidate_id = c.user_id "
                    "WHERE coalesce(p.is_searchable, true) AND c.is_open_to_offers"
                )
            )
        assert stored == searchable > 0
        calls = len(embedder.calls)
        assert await embed_command(settings, embedder=embedder) == 0
        # Второй запуск: одна проверка размерности и ни одного пересчёта карточек.
        assert len(embedder.calls) == calls + 1
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM match.candidate_embeddings WHERE model = :m"),
                {"m": embedder.model_name},
            )
        await engine.dispose()
