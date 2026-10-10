"""Швы LLM-стадии прогона: что прогон ждёт от переписчика, ранжировщика и объяснителя.

Реализации на LLM — matcher.llm_stage; тесты подставляют свои. Без стадий прогон идёт по
факторам и шаблонам. Стадия, которой модель не ответила, поднимает LLMUnavailableError, и
прогон продолжает без неё.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from matcher.factors import Factor
from matcher.profile_text import Card
from matcher.ranking import Ranked, Verdict
from matcher.repository import Need


class LLMUnavailableError(Exception):
    """Модель стадии не дала годного ответа: сеть, провайдер, ключ или ответ не по схеме."""


@dataclass(frozen=True)
class RankInput:
    candidate_id: uuid.UUID
    card: Card
    factors: Mapping[str, Factor]


class QueryRewriter(Protocol):
    """Карточка идеального кандидата по потребности и уточнениям работодателя.

    structured — карточка из структурных полей; refinements — уточнения цепочки прогонов от
    первого к последнему.
    """

    model: str

    async def rewrite(self, need: Need, structured: Card, refinements: Sequence[str]) -> Card: ...


class Ranker(Protocol):
    """Вердикт по каждому кандидату items; без вердикта по всем — LLMUnavailableError."""

    model: str

    async def score(
        self, need: Need, query: Card, items: list[RankInput], refinements: Sequence[str]
    ) -> dict[uuid.UUID, Verdict]: ...


class Explainer(Protocol):
    """Проверенный текст или текст по шаблону; ошибок не поднимает."""

    async def explain(self, item: Ranked) -> str: ...

    async def summarize(self, facts: dict[str, Any]) -> str: ...


@dataclass(frozen=True)
class RunStages:
    rewriter: QueryRewriter | None = None
    ranker: Ranker | None = None
    explainer: Explainer | None = None

    @property
    def uses_llm(self) -> bool:
        return any(stage is not None for stage in (self.rewriter, self.ranker, self.explainer))
