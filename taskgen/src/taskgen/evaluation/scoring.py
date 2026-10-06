"""Балл за задание: чистые функции без ввода-вывода.

Балл задания с кодом — взвешенная сумма компонентов из диапазона 0..1 с весами из
ScoringSettings, которые шаблон может переопределить. Задание с развёрнутым ответом
получает балл судьи без изменений.
"""

from __future__ import annotations

from collections.abc import Mapping
from statistics import fmean

from pydantic import BaseModel, Field

from taskgen.config import ScoringSettings
from taskgen.contracts import TestReport

# Компонент балла и поле ScoringSettings, которое задаёт его вес.
WEIGHT_OF = {
    "tests": "weight_tests",
    "rubric": "weight_rubric",
    "quality": "weight_quality",
    "performance": "weight_performance",
}


class Components(BaseModel):
    """Компоненты балла. None — компонент не измерен, например нет анализатора для языка."""

    tests: float = Field(default=0.0, ge=0.0, le=1.0)
    rubric: float | None = Field(default=None, ge=0.0, le=1.0)
    quality: float | None = Field(default=None, ge=0.0, le=1.0)
    performance: float | None = Field(default=None, ge=0.0, le=1.0)


def resolve_weights(settings: ScoringSettings, override: Mapping[str, float]) -> dict[str, float]:
    """Веса из настроек, поверх них — веса из шаблона.

    Шаблон может передать и другие поля ScoringSettings, но на балл задания влияют только веса.
    """
    weights = {name: getattr(settings, name) for name in WEIGHT_OF.values()}
    weights.update({name: value for name, value in override.items() if name in weights})
    return weights


def code_task_score(components: Components, weights: Mapping[str, float]) -> float:
    """Взвешенная сумма с жёстким гейтом: ни одного пройденного теста — балл 0.

    Вес неизмеренного компонента делится между измеренными. Иначе кандидат терял бы балл
    за то, что у сервиса нет анализатора для его языка или эталонного замера времени.
    """
    if components.tests == 0.0:
        return 0.0
    measured = {
        name: value
        for name, value in components.model_dump().items()
        if value is not None and weights[WEIGHT_OF[name]] > 0
    }
    total_weight = sum(weights[WEIGHT_OF[name]] for name in measured)
    if total_weight <= 0:
        raise ValueError("Все веса измеренных компонентов балла равны нулю")
    weighted = sum(weights[WEIGHT_OF[name]] * value for name, value in measured.items())
    return weighted / total_weight


def _ratio(reference: float, candidate: float, floor: float) -> float:
    # Порог шума: на малых значениях разница в разы — это дрожание замера, а не алгоритм.
    return min(1.0, max(reference, floor) / max(candidate, floor))


def performance_score(
    candidate: TestReport,
    reference: TestReport | None,
    *,
    time_floor_ms: int = 20,
    memory_floor_bytes: int = 1024 * 1024,
) -> float | None:
    """Скорость и память относительно эталона: 1 — не хуже эталона, 0.5 — вдвое хуже.

    Время сравнивается только на случаях, которые кандидат прошёл: случай, упавший сразу,
    не делает решение быстрым. Память в отчёте одна на весь прогон, поэтому сравнивается
    максимум. None — сравнить не с чем.
    """
    if reference is None:
        return None
    parts: list[float] = []
    pairs = [
        (own.wall_ms, ref.wall_ms)
        for own, ref in zip(candidate.outcomes, reference.outcomes, strict=False)
        if own.passed and own.wall_ms is not None and ref.wall_ms is not None
    ]
    if pairs:
        own_total = sum(own for own, _ in pairs)
        ref_total = sum(ref for _, ref in pairs)
        parts.append(_ratio(ref_total, own_total, time_floor_ms * len(pairs)))
    if candidate.max_memory_bytes is not None and reference.max_memory_bytes is not None:
        parts.append(
            _ratio(reference.max_memory_bytes, candidate.max_memory_bytes, memory_floor_bytes)
        )
    return fmean(parts) if parts else None
