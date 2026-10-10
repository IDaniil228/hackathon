"""Факты объяснения, барьер кодом и шаблоны объяснения и обзора прогона."""

import uuid
from datetime import timedelta

import pytest

from matcher.explain import (
    LABELS,
    explanation_facts,
    run_facts,
    template_summary,
)
from matcher.factors import CandidateFacts, FspResult, compute_factors, factor_score
from matcher.llm.evaluation.factcheck import barrier_problems
from matcher.ranking import Ranked, template_explanation
from tests.test_factors import NEED, NOW, candidate

CANDIDATES = {
    "fsp_and_fresh": candidate(
        fsp=(FspResult("Кубок", place=2, participants=40),),
        refreshed_at=NOW - timedelta(days=45),
        strength_percentile=0.37,
    ),
    "grade_gap": candidate(grade_position=3, grade_name="Lead", grade_confirmed=False),
    "salary_partial": candidate(salary_min=250, salary_max=400, currency="RUB"),
    "empty": candidate(grade_position=None, grade_name=None),
}


def _ranked(n: int, cand: CandidateFacts) -> Ranked:
    factors = compute_factors(NEED, cand, now=NOW)
    return Ranked(
        candidate_id=uuid.UUID(int=n),
        grade_confirmed=cand.grade_confirmed,
        factor_score=factor_score(factors),
        vector_score=None,
        factors=factors,
    )


@pytest.mark.parametrize("name", sorted(CANDIDATES))
def test_template_explanation_passes_the_barrier(name: str) -> None:
    item = _ranked(1, CANDIDATES[name])
    text = template_explanation(item.grade_confirmed, item.factors)
    assert barrier_problems(text, explanation_facts(item), labels=LABELS) == []


def test_numbers_of_factor_lines_are_facts() -> None:
    cand = candidate(
        fsp=(FspResult("Кубок ФСП 2026", place=2, participants=40),),
        refreshed_at=NOW - timedelta(days=45),
    )
    facts = explanation_facts(_ranked(1, cand))
    told = "Призёр Кубка ФСП 2026: 2 место из 40; профиль подтверждён 45 дн. назад."
    assert barrier_problems(told, facts, labels=LABELS) == []
    invented = "ФСП: 3 место из 40."
    assert barrier_problems(invented, facts, labels=LABELS) == ["числа 3 нет в фактах"]


@pytest.mark.parametrize(
    "text",
    [
        "Кандидат, вероятно, списывал.",
        "Код написан ИИ.",
        "Молодой специалист.",
        "Кандидату нужен возраст постарше.",
        "Подходит мужчина.",
        "Женщина с опытом.",
    ],
)
def test_labels_about_honesty_ai_age_and_gender_are_rejected(text: str) -> None:
    assert barrier_problems(text, {}, labels=LABELS)


def test_plain_explanation_has_no_label() -> None:
    assert not LABELS.search("Стек совпадает, грейд подтверждён тестом, вилка в бюджете.")


def test_run_summary_template_passes_the_barrier() -> None:
    items = [_ranked(n, cand) for n, cand in enumerate(CANDIDATES.values(), 1)]
    facts = run_facts(items, pool_size=12, vector_skipped=False)
    text = template_summary(facts)
    assert str(len(items)) in text and "12" in text
    assert barrier_problems(text, facts, labels=LABELS) == []


def test_run_facts_hold_only_the_top_of_the_list() -> None:
    items = [_ranked(n, CANDIDATES["empty"]) for n in range(1, 12)]
    facts = run_facts(items, pool_size=30, vector_skipped=False)
    assert facts["shortlist_size"] == 11
    assert [row["rank"] for row in facts["top"]] == [1, 2, 3, 4, 5]
