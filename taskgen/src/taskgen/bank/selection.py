"""Числовая часть отбора шаблонов, без БД: расстояние, softmax с температурой и выборка.

p ∝ exp(−d / T), d = alpha·|Δпоз| + beta·(1 − sim), T = t / (1 − t). Формула и смысл
параметров описаны в SelectionSettings (taskgen/config.py).
"""

from __future__ import annotations

import math
import random
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

# Расстояния считаются одной формулой из одинаковых входов, поэтому настоящие ничьи
# совпадают побитово; допуск гасит только ошибку округления при разном порядке операций.
_TIE_TOLERANCE = 1e-12


@dataclass(frozen=True)
class Draw:
    """Один выбранный кандидат и вероятность, с которой он выпал на своём шаге выборки."""

    index: int
    probability: float


def grade_positions(levels: Mapping[str, int]) -> dict[str, int]:
    """Позиция грейда в списке по возрастанию level.

    Расстояние между грейдами берётся по позициям: промежуточный грейд с level 15 не делает
    шаг junior → middle вдвое длиннее шага middle → senior.
    """
    ordered = sorted(levels, key=lambda code: levels[code])
    return {code: position for position, code in enumerate(ordered)}


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"векторы разной размерности: {len(a)} и {len(b)}")
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    if norm == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True)) / norm


def jaccard_similarity(a: Collection[str], b: Collection[str]) -> float:
    union = set(a) | set(b)
    if not union:
        return 0.0
    return len(set(a) & set(b)) / len(union)


def distance(grade_gap: int, similarity: float, *, alpha: float, beta: float) -> float:
    return alpha * abs(grade_gap) + beta * (1.0 - similarity)


def probabilities(distances: Sequence[float], t: float) -> list[float]:
    """Вероятности выбора каждого кандидата при температуре t ∈ [0, 1]."""
    if not 0.0 <= t <= 1.0:
        raise ValueError(f"температура должна лежать в [0, 1], получено {t}")
    n = len(distances)
    if n == 0:
        return []
    if t == 1.0:
        return [1.0 / n] * n
    nearest = min(distances)
    if t == 0.0:
        ties = [math.isclose(d, nearest, rel_tol=0.0, abs_tol=_TIE_TOLERANCE) for d in distances]
        return [1.0 / sum(ties) if tie else 0.0 for tie in ties]
    temperature = t / (1.0 - t)
    # Сдвиг на минимум не меняет распределение, но спасает exp от обнуления при больших d.
    weights = [math.exp(-(d - nearest) / temperature) for d in distances]
    total = sum(weights)
    return [w / total for w in weights]


def draw(distances: Sequence[float], k: int, t: float, rng: random.Random) -> list[Draw]:
    """Выбирает до k кандидатов без возвращения: на каждом шаге softmax по оставшимся."""
    remaining = list(range(len(distances)))
    picks: list[Draw] = []
    while remaining and len(picks) < k:
        p = probabilities([distances[i] for i in remaining], t)
        position = _sample(p, rng)
        picks.append(Draw(remaining[position], p[position]))
        del remaining[position]
    return picks


class Slot(StrEnum):
    """Откуда взят шаблон: из квоты заявленного грейда или из общего разведочного отбора."""

    QUOTA = "quota"
    EXPLORE = "explore"


@dataclass(frozen=True)
class SlotDraw:
    index: int
    probability: float
    slot: Slot


class QuotaError(ValueError):
    """Шаблонов заявленного грейда меньше квоты: тест такого состава собрать нельзя."""

    def __init__(self, available: int, quota: int) -> None:
        super().__init__(f"шаблонов заявленного грейда {available}, а квота требует {quota}")
        self.available = available
        self.quota = quota


def draw_with_quota(
    distances: Sequence[float],
    claimed: Sequence[bool],
    *,
    k: int,
    quota: int,
    t: float,
    rng: random.Random,
) -> list[SlotDraw]:
    """До k кандидатов: первые min(quota, k) — только из claimed, остальные — из всех.

    Внутри квоты работает тот же softmax по расстоянию, что и в разведке: при квоте без
    случайности утёкший список шаблонов грейда выдавал бы всем один и тот же шаблон.
    Разведочные слоты ограничены размером пула, квотные — нет: нехватка шаблонов
    заявленного грейда — ошибка банка, а не повод тихо взять чужой грейд.
    """
    quota = min(quota, k)
    pool = [i for i, own in enumerate(claimed) if own]
    if len(pool) < quota:
        raise QuotaError(len(pool), quota)
    picks = [
        SlotDraw(pool[pick.index], pick.probability, Slot.QUOTA)
        for pick in draw([distances[i] for i in pool], quota, t, rng)
    ]
    taken = {pick.index for pick in picks}
    rest = [i for i in range(len(distances)) if i not in taken]
    picks += [
        SlotDraw(rest[pick.index], pick.probability, Slot.EXPLORE)
        for pick in draw([distances[i] for i in rest], k - quota, t, rng)
    ]
    return picks


def _sample(p: Sequence[float], rng: random.Random) -> int:
    # Кандидат с нулевой вероятностью не должен выпасть даже из-за округления суммы.
    support = [i for i, x in enumerate(p) if x > 0.0]
    u = rng.random()
    acc = 0.0
    for i in support:
        acc += p[i]
        if u < acc:
            return i
    return support[-1]
