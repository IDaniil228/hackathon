"""Проверка цитат, которыми LLM-судья и проверяльщик ситуации эссе подтверждают вердикт.

Модель видит недоверенный текст и может ему подчиниться, поэтому её вердикт засчитывается
только с цитатой, которую код находит в тексте дословно.
"""

from __future__ import annotations

from collections import OrderedDict

from taskgen.contracts import Embedder


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
