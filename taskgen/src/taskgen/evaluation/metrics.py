"""Рубрики метрик стиля и вычисление quality.

Рубрика — md-файл в каталоге rubrics рядом с модулем, один на язык; имя файла — каноническое
имя языка из languages.py. Формат:

- до первого раздела строка "Размер решения: `ключ`" называет метрику размера, а строка
  "Минимальный размер: N" — нижнюю границу размера для нормировки;
- каждый раздел "## `инструмент.метрика`" описывает одну метрику. Ключ — имя инструмента
  из AnalysisReport.tool, точка и ключ из AnalysisReport.metrics;
- текст раздела идёт судье как трактовка метрики;
- пункты "- хорошо: X" и "- плохо: Y" задают линейную оценку: X и лучше даёт 1, Y и хуже — 0.
  Без них метрика остаётся фактом для судьи и в quality не входит;
- "- вес: W" — вес в среднем, по умолчанию 1;
- "- нормировка: на 100 строк" делит значение на размер решения и умножает на 100.

Новая метрика — это раздел в рубрике и анализатор, который отдаёт её ключ.
"""

from __future__ import annotations

import re
from functools import cache
from importlib import resources
from typing import Any

from pydantic import BaseModel

from taskgen.contracts import AnalysisReport
from taskgen.evaluation.languages import source_language

_HEADING = re.compile(r"^##\s+`?([^`\s]+)`?\s*$")
_RULE = re.compile(r"^-\s+([^:]+):\s*(.+?)\s*$")
_SIZE = re.compile(r"^Размер решения:\s*`?([^`\s]+)`?\s*$")
_MIN_SIZE = re.compile(r"^Минимальный размер:\s*(\d+)\s*$")
_PER_100_LINES = "на 100 строк"


class MetricRule(BaseModel):
    description: str
    good: float | None = None
    bad: float | None = None
    weight: float = 1.0
    per_100_lines: bool = False


class MetricRubric(BaseModel):
    size_metric: str | None = None
    min_size: int = 1
    metrics: dict[str, MetricRule]


class MetricScore(BaseModel):
    metric: str
    value: float
    normalized: float
    score: float
    weight: float


class QualityResult(BaseModel):
    score: float | None
    items: list[MetricScore]


def _finish(key: str, description: list[str], rules: dict[str, str]) -> MetricRule:
    known = {"хорошо", "плохо", "вес", "нормировка"}
    if unknown := rules.keys() - known:
        raise ValueError(f"Метрика {key}: неизвестные пункты {sorted(unknown)}")
    if ("хорошо" in rules) != ("плохо" in rules):
        raise ValueError(f"Метрика {key}: пункты 'хорошо' и 'плохо' задаются только вместе")
    norm = rules.get("нормировка")
    if norm is not None and norm != _PER_100_LINES:
        raise ValueError(f"Метрика {key}: нормировка бывает только '{_PER_100_LINES}'")
    rule = MetricRule(
        description="\n".join(description),
        good=float(rules["хорошо"]) if "хорошо" in rules else None,
        bad=float(rules["плохо"]) if "плохо" in rules else None,
        weight=float(rules.get("вес", 1.0)),
        per_100_lines=norm is not None,
    )
    if rule.good is not None and rule.good == rule.bad:
        raise ValueError(f"Метрика {key}: 'хорошо' и 'плохо' совпадают")
    return rule


def parse_rubric(text: str) -> MetricRubric:
    size_metric: str | None = None
    min_size = 1
    metrics: dict[str, MetricRule] = {}
    key: str | None = None
    description: list[str] = []
    rules: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if heading := _HEADING.match(line):
            if key is not None:
                metrics[key] = _finish(key, description, rules)
            key, description, rules = heading.group(1), [], {}
        elif key is None:
            if size := _SIZE.match(line):
                size_metric = size.group(1)
            elif minimum := _MIN_SIZE.match(line):
                min_size = int(minimum.group(1))
        elif rule := _RULE.match(line):
            rules[rule.group(1).strip().lower()] = rule.group(2)
        elif line:
            description.append(line)
    if key is not None:
        metrics[key] = _finish(key, description, rules)
    return MetricRubric(size_metric=size_metric, min_size=min_size, metrics=metrics)


@cache
def load_rubric(language: str | None) -> MetricRubric | None:
    """Рубрика для языка из пакета или None, если для языка рубрики нет."""
    name = source_language(language)
    if name is None:
        return None
    path = resources.files("taskgen.evaluation") / "rubrics" / f"{name}.md"
    if not path.is_file():
        return None
    return parse_rubric(path.read_text(encoding="utf-8"))


def _flatten(reports: list[AnalysisReport]) -> dict[str, float]:
    return {
        f"{report.tool}.{name}": value
        for report in reports
        for name, value in report.metrics.items()
    }


def _interpolate(value: float, good: float, bad: float) -> float:
    # Работает в обе стороны: для сложности хорошо меньше, для доли аннотаций — больше.
    return min(1.0, max(0.0, (bad - value) / (bad - good)))


def compute_quality(reports: list[AnalysisReport], rubric: MetricRubric) -> QualityResult:
    """Взвешенное среднее оценок метрик, для которых в рубрике есть границы.

    Метрика без значения в отчётах пропускается, а не считается нулём: отсутствующий
    инструмент не должен снижать балл. Нет ни одной оценённой метрики — quality неизвестно.
    """
    values = _flatten(reports)
    size = values.get(rubric.size_metric) if rubric.size_metric else None
    items: list[MetricScore] = []
    for key, rule in rubric.metrics.items():
        if rule.good is None or rule.bad is None or key not in values:
            continue
        value = values[key]
        normalized = value
        if rule.per_100_lines:
            if size is None:
                continue
            normalized = value * 100 / max(size, rubric.min_size)
        items.append(
            MetricScore(
                metric=key,
                value=value,
                normalized=normalized,
                score=_interpolate(normalized, rule.good, rule.bad),
                weight=rule.weight,
            )
        )
    total_weight = sum(item.weight for item in items)
    if total_weight <= 0:
        return QualityResult(score=None, items=items)
    score = sum(item.score * item.weight for item in items) / total_weight
    return QualityResult(score=score, items=items)


def judge_facts(reports: list[AnalysisReport], rubric: MetricRubric | None) -> list[dict[str, Any]]:
    """Значения метрик с трактовкой из рубрики: судья видит число и что оно значит."""
    described = rubric.metrics if rubric else {}
    return [
        {
            "метрика": key,
            "значение": value,
            "смысл": described[key].description if key in described else None,
        }
        for key, value in sorted(_flatten(reports).items())
    ]
