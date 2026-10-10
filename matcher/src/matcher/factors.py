"""Детерминированные факторы соответствия кандидата потребности.

У каждого фактора есть значение 0..1 и строка объяснения по-русски. Применим ли фактор,
решает запрос: без навыков в потребности стек не считается, без грейда — грейд, без вилки —
вилка. Отсутствие данных у кандидата даёт нейтральное значение, а не исключение: префильтр
уже отсеял тех, кто не подходит по жёстким условиям. factor_score — среднее применимых
факторов; без LLM он задаёт порядок, с LLM он — факт для ранжировщика и ключ ничьих.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

# Значение фактора, о котором у кандидата нет данных: не поднимает и не опускает его.
NEUTRAL = 0.5
# Навык заявлен, но не подтверждён тестом: половина веса подтверждённого.
CLAIMED_SHARE = 0.5
# Желательный навык весит половину обязательного с тем же весом релевантности.
NICE_TO_HAVE_SHARE = 0.5
# Вес навыка, для которого match.skill_relevance ещё не пересчитан (fspdb relevance).
DEFAULT_SKILL_WEIGHT = 1.0
# Каждая ступень грейда между кандидатом и потребностью снимает половину значения.
GRADE_STEP_PENALTY = 0.5
# Профиль, обновлённый не раньше FRESH_DAYS назад, свеж полностью; к STALE_DAYS свежесть
# линейно падает до нуля.
FRESH_DAYS = 30
STALE_DAYS = 365


@dataclass(frozen=True)
class NeedSkill:
    skill_id: int
    name: str
    required: bool
    min_level: int | None
    # Вес из match.skill_relevance для специализации потребности; None — веса нет.
    weight: float | None


@dataclass(frozen=True)
class NeedFacts:
    skills: tuple[NeedSkill, ...]
    # Позиция грейда в лестнице отрасли, упорядоченной по ref.grades.level.
    grade_position: int | None
    grade_name: str | None
    salary_min: int | None
    salary_max: int | None
    currency: str


@dataclass(frozen=True)
class CandidateSkill:
    self_level: int
    confirmed_level: int | None


@dataclass(frozen=True)
class FspResult:
    event: str
    place: int | None
    participants: int | None


@dataclass(frozen=True)
class CandidateFacts:
    skills: dict[int, CandidateSkill]
    grade_position: int | None
    grade_name: str | None
    grade_confirmed: bool
    # None во всех трёх полях — кандидат не указал ожиданий.
    salary_min: int | None
    salary_max: int | None
    currency: str | None
    # Доля кандидатов категории с меньшей силой профиля; None — категории нет или он один.
    strength_percentile: float | None
    # Пусто и без истории ФСП, и когда кандидат скрыл её: объяснение их не различает.
    fsp: tuple[FspResult, ...]
    refreshed_at: datetime | None


@dataclass(frozen=True)
class Factor:
    value: float
    line: str
    applicable: bool = True

    def as_json(self) -> dict[str, object]:
        return {"value": round(self.value, 4), "line": self.line, "applicable": self.applicable}


def _skill_value(need: NeedSkill, have: CandidateSkill | None) -> float:
    if have is None:
        return 0.0
    minimum = need.min_level or 0
    if have.confirmed_level is not None and have.confirmed_level >= minimum:
        return 1.0
    # Подтверждённый уровень ниже нужного перекрывает заявленный: тест сильнее самооценки.
    if have.confirmed_level is None and have.self_level >= minimum:
        return CLAIMED_SHARE
    return 0.0


def _names(skills: list[NeedSkill]) -> str:
    return ", ".join(skill.name for skill in skills)


def stack_factor(need: NeedFacts, cand: CandidateFacts) -> Factor:
    if not need.skills:
        return Factor(NEUTRAL, "Стек: в потребности нет навыков", applicable=False)
    total = earned = 0.0
    groups: dict[float, list[NeedSkill]] = {1.0: [], CLAIMED_SHARE: [], 0.0: []}
    for skill in need.skills:
        weight = DEFAULT_SKILL_WEIGHT if skill.weight is None else skill.weight
        if not skill.required:
            weight *= NICE_TO_HAVE_SHARE
        score = _skill_value(skill, cand.skills.get(skill.skill_id))
        groups[score].append(skill)
        total += weight
        earned += weight * score
    parts = [
        f"{label} {_names(groups[key])}"
        for key, label in ((1.0, "подтверждены"), (CLAIMED_SHARE, "заявлены"), (0.0, "нет"))
        if groups[key]
    ]
    value = earned / total if total > 0 else 0.0
    return Factor(value, "Стек: " + "; ".join(parts))


def grade_factor(need: NeedFacts, cand: CandidateFacts) -> Factor:
    status = "подтверждён тестом" if cand.grade_confirmed else "не подтверждён тестом"
    if need.grade_position is None:
        return Factor(NEUTRAL, "Грейд: в потребности не задан", applicable=False)
    if cand.grade_position is None:
        return Factor(NEUTRAL, "Грейд: у кандидата нет грейда")
    gap = cand.grade_position - need.grade_position
    value = max(0.0, 1.0 - GRADE_STEP_PENALTY * abs(gap))
    if gap == 0:
        relation = "совпадает с потребностью"
    else:
        relation = f"на {abs(gap)} ступ. {'выше' if gap > 0 else 'ниже'} {need.grade_name}"
    return Factor(value, f"Грейд: {cand.grade_name}, {relation}, {status}")


def _overlap_share(c_lo: float, c_hi: float, n_lo: float, n_hi: float) -> float:
    """Доля ожиданий кандидата [c_lo, c_hi], которая попадает в бюджет [n_lo, n_hi]."""
    if c_hi <= c_lo:
        return 1.0 if n_lo <= c_lo <= n_hi else 0.0
    overlap = min(c_hi, n_hi) - max(c_lo, n_lo)
    if overlap < 0:
        return 0.0
    if math.isinf(c_hi - c_lo):
        return 1.0
    return min(1.0, overlap / (c_hi - c_lo))


def salary_factor(need: NeedFacts, cand: CandidateFacts) -> Factor:
    if need.salary_min is None and need.salary_max is None:
        return Factor(NEUTRAL, "Вилка: в потребности не задана", applicable=False)
    if cand.salary_min is None and cand.salary_max is None:
        return Factor(NEUTRAL, "Вилка: кандидат не указал ожиданий")
    if cand.currency != need.currency:
        return Factor(NEUTRAL, "Вилка: ожидания кандидата в другой валюте")
    n_lo = need.salary_min if need.salary_min is not None else -math.inf
    n_hi = need.salary_max if need.salary_max is not None else math.inf
    c_lo = cand.salary_min if cand.salary_min is not None else n_lo
    c_hi = cand.salary_max if cand.salary_max is not None else n_hi
    value = _overlap_share(c_lo, c_hi, n_lo, n_hi)
    if value >= 1.0:
        line = "Вилка: ожидания кандидата в бюджете потребности"
    elif value > 0.0:
        line = f"Вилка: в бюджет попадает {round(value * 100)} % ожиданий кандидата"
    else:
        line = "Вилка: ожидания кандидата вне бюджета потребности"
    return Factor(value, line)


def percentile_factor(cand: CandidateFacts) -> Factor:
    if cand.strength_percentile is None:
        return Factor(NEUTRAL, "Сила профиля: не с кем сравнить в категории")
    share = round(cand.strength_percentile * 100)
    return Factor(cand.strength_percentile, f"Сила профиля выше, чем у {share} % категории")


def fsp_factor(cand: CandidateFacts) -> Factor:
    placed = [r for r in cand.fsp if r.place is not None and r.participants]
    if not placed:
        if cand.fsp:
            return Factor(NEUTRAL, f"ФСП: участие, {cand.fsp[0].event}")
        return Factor(NEUTRAL, "ФСП: нет данных")
    best = max(placed, key=lambda r: 1.0 - (r.place - 1) / r.participants)
    relative = 1.0 - (best.place - 1) / best.participants
    value = NEUTRAL + (1.0 - NEUTRAL) * relative
    return Factor(value, f"ФСП: {best.place} место из {best.participants}, {best.event}")


def freshness_factor(cand: CandidateFacts, now: datetime) -> Factor:
    if cand.refreshed_at is None:
        return Factor(NEUTRAL, "Свежесть: профиль не подтверждался")
    days = max(0, (now - cand.refreshed_at).days)
    value = min(1.0, max(0.0, (STALE_DAYS - days) / (STALE_DAYS - FRESH_DAYS)))
    return Factor(value, f"Свежесть: профиль подтверждён {days} дн. назад")


# Смысл каждого фактора compute_factors для модели: его читают писатель и проверяльщик
# объяснения выдачи (explain.py).
MEANING = {
    "stack": "навыки потребности у кандидата: подтверждены тестом, заявлены или нет",
    "grade": "грейд кандидата рядом с грейдом потребности",
    "salary": "ожидания кандидата по зарплате рядом с бюджетом потребности",
    "percentile": "сила профиля кандидата среди кандидатов его категории",
    "fsp": "результаты кандидата в соревнованиях ФСП",
    "freshness": "давность подтверждения профиля",
}


def compute_factors(need: NeedFacts, cand: CandidateFacts, *, now: datetime) -> dict[str, Factor]:
    return {
        "stack": stack_factor(need, cand),
        "grade": grade_factor(need, cand),
        "salary": salary_factor(need, cand),
        "percentile": percentile_factor(cand),
        "fsp": fsp_factor(cand),
        "freshness": freshness_factor(cand, now),
    }


def factor_score(factors: dict[str, Factor]) -> float:
    applicable = [factor.value for factor in factors.values() if factor.applicable]
    return sum(applicable) / len(applicable) if applicable else NEUTRAL
