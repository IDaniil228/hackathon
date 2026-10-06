"""Кэш эмбеддингов описаний текущих шаблонов в template_embeddings.

Строка кэша актуальна, если есть запись для пары (шаблон, модель) и её content_hash равен
SHA-256 текста, который ушёл в эмбеддер. Хэш берётся от текста эмбеддинга, а не от всего md:
правка эталона не должна тратить вызов эмбеддера.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from taskgen.bank.parser import content_hash
from taskgen.contracts import Embedder
from taskgen.db import Template, TemplateEmbedding

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 64


@dataclass(frozen=True)
class _Pending:
    template_id: uuid.UUID
    text: str


def embedding_text(description: str) -> str:
    """Текст, по которому считается эмбеддинг шаблона. Отбор сравнивает его с навыками."""
    return description


async def refresh_embeddings(
    session: AsyncSession, embedder: Embedder, *, batch_size: int = DEFAULT_BATCH_SIZE
) -> int:
    """Досчитывает недостающие эмбеддинги в сессии вызывающего; ошибку эмбеддера пробрасывает."""
    written = 0
    pending = await _pending(session, embedder.model_name)
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        vectors = await _embed(embedder, batch)
        await _store(session, embedder.model_name, batch, vectors)
        written += len(batch)
    return written


async def refresh_embeddings_safely(
    sessionmaker: async_sessionmaker[AsyncSession],
    embedder: Embedder,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """То же, но каждый батч фиксируется отдельно, а ошибка только пишется в лог.

    Импорт не зависит от эмбеддера: шаблон без вектора остаётся в банке, а отбор переходит
    на сходство по навыкам. Уже посчитанные батчи сохраняются, следующий запуск досчитает
    остальное.
    """
    written = 0
    try:
        async with sessionmaker() as session:
            pending = await _pending(session, embedder.model_name)
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            vectors = await _embed(embedder, batch)
            async with sessionmaker() as session:
                await _store(session, embedder.model_name, batch, vectors)
                await session.commit()
            written += len(batch)
    except Exception:
        logger.exception(
            "Эмбеддинги шаблонов посчитаны не полностью (модель %s, записано %d); "
            "отбор для шаблонов без вектора пойдёт по сходству навыков",
            embedder.model_name,
            written,
        )
    return written


async def _pending(session: AsyncSession, model: str) -> list[_Pending]:
    rows = await session.execute(
        select(Template.id, Template.description, TemplateEmbedding.content_hash)
        .outerjoin(
            TemplateEmbedding,
            and_(TemplateEmbedding.template_id == Template.id, TemplateEmbedding.model == model),
        )
        .where(Template.is_current)
        .order_by(Template.name)
    )
    pending = []
    for template_id, description, cached_hash in rows:
        text = embedding_text(description)
        if cached_hash != content_hash(text):
            pending.append(_Pending(template_id, text))
    return pending


async def _embed(embedder: Embedder, batch: Sequence[_Pending]) -> list[list[float]]:
    vectors = await embedder.embed([item.text for item in batch])
    if len(vectors) != len(batch):
        raise ValueError(f"эмбеддер вернул {len(vectors)} векторов на {len(batch)} текстов")
    if any(not vector for vector in vectors):
        raise ValueError("эмбеддер вернул пустой вектор")
    return vectors


async def _store(
    session: AsyncSession, model: str, batch: Sequence[_Pending], vectors: list[list[float]]
) -> None:
    statement = insert(TemplateEmbedding).values(
        [
            {
                "template_id": item.template_id,
                "model": model,
                "content_hash": content_hash(item.text),
                "embedding": vector,
            }
            for item, vector in zip(batch, vectors, strict=True)
        ]
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[TemplateEmbedding.template_id, TemplateEmbedding.model],
            set_={
                "content_hash": statement.excluded.content_hash,
                "embedding": statement.excluded.embedding,
            },
        )
    )
