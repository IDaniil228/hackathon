"""Метрики бенчмарка подбора: nDCG@k, recall@K и сравнение позиций двух кандидатов.

Кандидат без метки в labels — чужой профиль в пуле, о релевантности которого набор ничего не
знает. Метрики его пропускают: выдача сжимается до размеченных кандидатов (condensed list,
приём оценки при неполной разметке).
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable, Mapping, Sequence
from math import log2


def _dcg(gains: Iterable[int]) -> float:
    # Экспоненциальное усиление 2^rel - 1: идеальный кандидат весит заметно больше близкого.
    return sum((2**gain - 1) / log2(position + 2) for position, gain in enumerate(gains))


def ndcg_at(ranking: Sequence[Hashable], labels: Mapping[Hashable, int], *, k: int) -> float | None:
    """nDCG@k выдачи ranking; None, если в labels нет ни одного релевантного кандидата.

    Идеальный DCG строится по всем меткам labels, поэтому релевантный кандидат, которого
    в выдаче нет, снижает значение.
    """
    ideal = _dcg(sorted(labels.values(), reverse=True)[:k])
    if ideal == 0:
        return None
    seen = [labels[cid] for cid in ranking if cid in labels][:k]
    return _dcg(seen) / ideal


def recall(
    top: Iterable[Hashable], labels: Mapping[Hashable, int], *, threshold: int
) -> float | None:
    """Доля кандидатов с меткой не ниже threshold, которые есть в top; None — таких нет."""
    relevant = {cid for cid, label in labels.items() if label >= threshold}
    if not relevant:
        return None
    return len(relevant & set(top)) / len(relevant)


def below(ranking: Sequence[Hashable], lower: Hashable, upper: Hashable) -> bool:
    """lower стоит в выдаче ниже upper; кандидат вне выдачи ниже всех."""
    position = {cid: index for index, cid in enumerate(ranking)}
    missing = len(ranking)
    return position.get(lower, missing) > position.get(upper, missing)
