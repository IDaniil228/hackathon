"""Кэш векторов карточек в match.candidate_embeddings и match.need_embeddings.

Строка кэша актуальна, если есть запись для пары (сущность, модель) и её source_hash равен
SHA-256 текста карточки (profile_text.source_hash). Модель — имя без провайдера, как у
taskgen: векторы задаёт модель, а не провайдер. Приём тот же, что в
taskgen/src/taskgen/bank/embeddings.py.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from matcher.llm.contracts import Embedder
from matcher.profile_text import Card, render_profile, source_hash

logger = logging.getLogger(__name__)

# Текст для проверки размерности при старте: сам вектор не нужен, только его длина.
_PROBE = "проверка размерности"


class DimensionMismatchError(RuntimeError):
    """Модель эмбеддингов даёт векторы не той длины, что колонка в схеме api."""


def vector_literal(vector: Sequence[float]) -> str:
    """Вектор в текстовом виде pgvector; в запросе он приводится CAST(... AS vector)."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


_COLUMN_DIM = text(
    """
    SELECT atttypmod FROM pg_attribute
    WHERE attrelid = 'match.candidate_embeddings'::regclass AND attname = 'embedding'
    """
)


async def column_dimension(session: AsyncSession) -> int:
    return await session.scalar(_COLUMN_DIM)


async def check_dimension(session: AsyncSession, embedder: Embedder) -> int:
    """Сверяет длину вектора модели с типом колонки; возвращает размерность."""
    expected = await column_dimension(session)
    [vector] = await embedder.embed([_PROBE])
    if len(vector) != expected:
        raise DimensionMismatchError(
            f"модель {embedder.model_name} даёт векторы длины {len(vector)}, а колонка "
            f"match.candidate_embeddings.embedding — vector({expected}). Смените модель "
            "llm.embedding или размерность колонки миграцией api"
        )
    return expected


async def _embed(embedder: Embedder, texts: list[str]) -> list[list[float]]:
    vectors = await embedder.embed(texts)
    if len(vectors) != len(texts):
        raise ValueError(f"эмбеддер вернул {len(vectors)} векторов на {len(texts)} текстов")
    if any(not vector for vector in vectors):
        raise ValueError("эмбеддер вернул пустой вектор")
    return vectors


_CANDIDATE_HASHES = text(
    """
    SELECT candidate_id, source_hash FROM match.candidate_embeddings
    WHERE model = :model AND candidate_id = ANY(:ids)
    """
)

_UPSERT_CANDIDATE = text(
    """
    INSERT INTO match.candidate_embeddings (candidate_id, model, embedding, source_hash, built_at)
    VALUES (:entity_id, :model, CAST(:embedding AS vector), :source_hash, now())
    ON CONFLICT (candidate_id, model) DO UPDATE
    SET embedding = excluded.embedding, source_hash = excluded.source_hash,
        built_at = excluded.built_at
    """
)


async def refresh_candidates(
    sessionmaker: async_sessionmaker[AsyncSession],
    embedder: Embedder,
    cards: Mapping[uuid.UUID, Card],
    *,
    batch_size: int,
) -> int:
    """Досчитывает векторы карточек, у которых нет актуальной строки кэша.

    Пустая карточка вектора не получает: сравнивать в ней нечего. Возвращает, сколько
    векторов записано; ошибку эмбеддера пробрасывает. Эмбеддер вызывается без занятого
    соединения, каждый батч пишется своей короткой транзакцией.
    """
    texts = {cid: render_profile(card) for cid, card in cards.items()}
    texts = {cid: body for cid, body in texts.items() if body}
    async with sessionmaker() as session:
        cached = {
            row.candidate_id: row.source_hash
            for row in await session.execute(
                _CANDIDATE_HASHES, {"model": embedder.model_name, "ids": list(texts)}
            )
        }
    pending = sorted(cid for cid, body in texts.items() if cached.get(cid) != source_hash(body))
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        vectors = await _embed(embedder, [texts[cid] for cid in batch])
        async with sessionmaker() as session, session.begin():
            await session.execute(
                _UPSERT_CANDIDATE,
                [
                    {
                        "entity_id": cid,
                        "model": embedder.model_name,
                        "embedding": vector_literal(vector),
                        "source_hash": source_hash(texts[cid]),
                    }
                    for cid, vector in zip(batch, vectors, strict=True)
                ],
            )
    if pending:
        logger.info("Векторы карточек: записано %d, модель %s", len(pending), embedder.model_name)
    return len(pending)


_NEED_CACHED = text(
    """
    SELECT source_hash, embedding::text AS embedding FROM match.need_embeddings
    WHERE need_id = :need_id AND model = :model
    """
)

_UPSERT_NEED = text(
    """
    INSERT INTO match.need_embeddings (need_id, model, embedding, source_hash, built_at)
    VALUES (:need_id, :model, CAST(:embedding AS vector), :source_hash, now())
    ON CONFLICT (need_id, model) DO UPDATE
    SET embedding = excluded.embedding, source_hash = excluded.source_hash,
        built_at = excluded.built_at
    """
)


async def need_vector(
    sessionmaker: async_sessionmaker[AsyncSession],
    embedder: Embedder,
    need_id: uuid.UUID,
    card: Card,
) -> tuple[list[float], str]:
    """Вектор карточки запроса из кэша или от эмбеддера; второе значение — source_hash.

    Эмбеддер вызывается без занятого соединения.
    """
    body = render_profile(card)
    digest = source_hash(body)
    async with sessionmaker() as session:
        row = (
            await session.execute(_NEED_CACHED, {"need_id": need_id, "model": embedder.model_name})
        ).first()
    if row is not None and row.source_hash == digest:
        return [float(x) for x in row.embedding.strip("[]").split(",")], digest
    [vector] = await _embed(embedder, [body])
    async with sessionmaker() as session, session.begin():
        await session.execute(
            _UPSERT_NEED,
            {
                "need_id": need_id,
                "model": embedder.model_name,
                "embedding": vector_literal(vector),
                "source_hash": digest,
            },
        )
    return vector, digest


_SCORES = text(
    """
    SELECT candidate_id, 1 - (embedding <=> CAST(:query AS vector)) AS score
    FROM match.candidate_embeddings
    WHERE model = :model AND candidate_id = ANY(:pool)
    ORDER BY embedding <=> CAST(:query AS vector), candidate_id
    """
)


async def vector_scores(
    session: AsyncSession, model: str, query: Sequence[float], pool: Sequence[uuid.UUID]
) -> dict[uuid.UUID, float]:
    """Косинусная близость карточки запроса к векторам кандидатов пула, от ближних к дальним.

    Близость считается точно по всем векторам пула, без LIMIT: пул уже отфильтрован, а
    близость остальных кандидатов нужна следу прогона в search_results.
    """
    rows = await session.execute(
        _SCORES, {"query": vector_literal(query), "model": model, "pool": list(pool)}
    )
    return {row.candidate_id: float(row.score) for row in rows}
