"""Порядок короткого списка, разведка и объяснение по шаблону. Без БД и без сети."""

from __future__ import annotations

import random
import uuid
from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from matcher.factors import Factor

# Предел колонки match.search_results.explanation, varchar(2000) в схеме api.
EXPLANATION_LIMIT = 2000


@dataclass(frozen=True)
class Verdict:
    """Ответ LLM-ранжировщика об одном кандидате."""

    # Насколько кандидат подходит, 0..1.
    score: float
    # Фрагменты негативного промта работодателя, которым кандидат соответствует.
    need_negative_hits: list[str]
    # Фрагменты негативного промта кандидата, которым соответствует потребность.
    candidate_negative_hits: list[str]
    # Черновик объяснения для работодателя; до выдачи его проверяет explain.
    sentences: list[str]


@dataclass
class Ranked:
    candidate_id: uuid.UUID
    grade_confirmed: bool
    factor_score: float
    vector_score: float | None
    factors: dict[str, Factor]
    # None — LLM-стадии не было или она недоступна.
    verdict: Verdict | None = None
    explanation: str | None = None

    @property
    def llm_score(self) -> float | None:
        return None if self.verdict is None else self.verdict.score


def _order_key(item: Ranked) -> tuple[Any, ...]:
    def descending(score: float | None) -> float:
        return -score if score is not None else float("inf")

    return (
        # Неподтверждённый грейд всегда ниже подтверждённых, без коэффициента.
        not item.grade_confirmed,
        descending(item.llm_score),
        -item.factor_score,
        descending(item.vector_score),
        item.candidate_id,
    )


def order(items: Sequence[Ranked]) -> list[Ranked]:
    """Подтверждённый грейд, затем llm_score, factor_score, vector_score; ничья — по id."""
    return sorted(items, key=_order_key)


def template_explanation(grade_confirmed: bool, factors: Mapping[str, Factor]) -> str:
    """Объяснение из строк применимых факторов; влезает в колонку explanation.

    Подтверждение грейда называет строка фактора grade; без применимого фактора grade оно
    стоит первой строкой.
    """
    lines = []
    grade = factors.get("grade")
    if grade is None or not grade.applicable:
        status = "подтверждён" if grade_confirmed else "не подтверждён"
        lines.append(f"Грейд {status} тестом")
    lines += [factor.line for factor in factors.values() if factor.applicable]
    text = ". ".join(lines) + "."
    if len(text) > EXPLANATION_LIMIT:
        text = text[: EXPLANATION_LIMIT - 1] + "…"
    return text


@dataclass(frozen=True)
class Exploration:
    fresh: list[uuid.UUID] = field(default_factory=list)
    random: list[uuid.UUID] = field(default_factory=list)

    def all(self) -> list[uuid.UUID]:
        return [*self.fresh, *self.random]


def explore(
    rest: Sequence[uuid.UUID],
    created: Mapping[uuid.UUID, Hashable],
    *,
    run_id: uuid.UUID,
    fresh: int,
    random_count: int,
) -> Exploration:
    """Самые новые профили из rest и случайные из оставшихся.

    Случайный выбор зависит только от run_id и множества rest: повтор прогона с тем же id
    даёт тех же кандидатов.
    """
    by_age = sorted(rest, key=lambda cid: (created[cid], cid), reverse=True)
    newest = by_age[:fresh]
    remaining = sorted(set(rest) - set(newest))
    rng = random.Random(str(run_id))
    picked = rng.sample(remaining, min(random_count, len(remaining)))
    return Exploration(fresh=newest, random=picked)


def needs_vectors(pool_size: int, *, top_k: int) -> bool:
    """Нужна ли векторная стадия: пул не больше top_k целиком идёт дальше и без неё."""
    return pool_size > top_k


@dataclass(frozen=True)
class Shortlist:
    # Векторный top_k от ближних к дальним; при пропуске стадии пусто.
    top: list[uuid.UUID]
    exploration: Exploration
    # Кандидаты пула, которые дальше не идут.
    rest: list[uuid.UUID]
    vector_skipped: bool

    @property
    def ranked(self) -> list[uuid.UUID]:
        """Короткий список: его кандидаты получают факторы, ранг и объяснение."""
        return [*self.top, *self.exploration.all()]


def shortlist(
    pool: Mapping[uuid.UUID, Hashable],
    scores: Mapping[uuid.UUID, float] | None,
    *,
    top_k: int,
    run_id: uuid.UUID,
    fresh: int,
    random_count: int,
) -> Shortlist:
    """Короткий список из пула: pool — id → дата создания профиля, scores — близость.

    Кандидат без вектора в top_k не входит, но разведка может его взять.
    """
    if not needs_vectors(len(pool), top_k=top_k):
        return Shortlist(top=sorted(pool), exploration=Exploration(), rest=[], vector_skipped=True)
    scores = scores or {}
    by_score = sorted(scores, key=lambda cid: (-scores[cid], cid))
    top = [cid for cid in by_score if cid in pool][:top_k]
    outside = [cid for cid in pool if cid not in set(top)]
    picked = explore(outside, pool, run_id=run_id, fresh=fresh, random_count=random_count)
    taken = set(top) | set(picked.all())
    rest = sorted(cid for cid in pool if cid not in taken)
    return Shortlist(top=top, exploration=picked, rest=rest, vector_skipped=False)
