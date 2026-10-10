# Копия taskgen/src/taskgen/evaluation/quotes.py от matcher/scripts/sync_llm.py, не править
"""Проверка цитат, которыми LLM-судья и проверяльщик ситуации эссе подтверждают вердикт.

Модель видит недоверенный текст и может ему подчиниться, поэтому её вердикт засчитывается
только с цитатой, которую код находит в тексте дословно. Модель угроз и замер порога
проверки смысла — docs/taskgen/security.md.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from matcher.llm.bank.selection import cosine_similarity
from matcher.llm.contracts import Embedder


def _normalize(text: str) -> str:
    return " ".join(text.split())


def quote_in_answer(quote: str, answer: str) -> bool:
    """Цитата есть в ответе с точностью до пробельных символов: модели переносят строки."""
    normalized = _normalize(quote)
    return bool(normalized) and normalized in _normalize(answer)


class EmbeddingCache:
    """Векторы текстов шаблона: фактов, запрещённых фраз и пунктов рубрики.

    Эти тексты одинаковы во всех экземплярах и ответах. Ключ — модель эмбеддера и текст,
    поэтому смена модели не отдаёт чужие векторы. Старые записи вытесняются, когда их
    больше max_items.
    """

    def __init__(self, max_items: int = 4096) -> None:
        self.max_items = max_items
        self._vectors: OrderedDict[tuple[str, str], list[float]] = OrderedDict()

    async def vectors(self, embedder: Embedder, texts: list[str]) -> list[list[float]]:
        model = embedder.model_name
        found: dict[str, list[float]] = {}
        for text in texts:
            vector = self._vectors.get((model, text))
            if vector is not None:
                self._vectors.move_to_end((model, text))
                found[text] = vector
        missing = [text for text in dict.fromkeys(texts) if text not in found]
        if missing:
            fresh = await embedder.embed(missing)
            for text, vector in zip(missing, fresh, strict=True):
                found[text] = vector
                self._vectors[(model, text)] = vector
            while len(self._vectors) > self.max_items:
                self._vectors.popitem(last=False)
        return [found[text] for text in texts]


def state_embedding_cache(state: Any) -> EmbeddingCache:
    """Кэш из app.state.embedding_cache, общий для генерации и оценки.

    Генератор и оценщик собираются на каждый запрос, и свой кэш пустел бы после каждого.
    """
    cache = getattr(state, "embedding_cache", None)
    if cache is None:
        cache = state.embedding_cache = EmbeddingCache()
    return cache


class RelevanceUnavailableError(Exception):
    """Проверку смысла нельзя выполнить: эмбеддера нет или он вернул ошибку."""


@dataclass
class QuoteRelevance:
    """Проверка смысла цитат: цитаты вместе похожи на текст пункта рубрики или факта.

    Дословная цитата доказывает только наличие текста в ответе. Кандидат может дописать
    фразу, в которой сам заявляет о выполнении пункта, и обманутая модель её процитирует.
    Порог косинусного сходства отсекает цитату не по теме пункта.
    """

    threshold: float
    embedder: Embedder | None
    cache: EmbeddingCache = field(default_factory=EmbeddingCache)

    async def similarities(self, pairs: list[tuple[str, list[str]]]) -> list[float]:
        """Сходство каждой пары "текст пункта, цитаты"; цитаты пары склеиваются в один текст.

        Векторы пунктов берутся из кэша: они одинаковы для всех ответов на шаблон.
        """
        if not pairs:
            return []
        if self.embedder is None:
            raise RelevanceUnavailableError("эмбеддер не настроен")
        try:
            references = await self.cache.vectors(self.embedder, [text for text, _ in pairs])
            quoted = await self.embedder.embed([" ".join(quotes) for _, quotes in pairs])
        except Exception as error:
            raise RelevanceUnavailableError(f"эмбеддер вернул ошибку: {error}") from error
        return [
            cosine_similarity(reference, quote)
            for reference, quote in zip(references, quoted, strict=True)
        ]
