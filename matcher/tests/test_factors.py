from datetime import UTC, datetime, timedelta

import pytest

from matcher.factors import (
    NEUTRAL,
    CandidateFacts,
    CandidateSkill,
    FspResult,
    NeedFacts,
    NeedSkill,
    compute_factors,
    factor_score,
)

NOW = datetime(2026, 10, 10, tzinfo=UTC)

# A — обязательный навык с весом 0.8, B — желательный с весом 0.4: в сумме весов он идёт
# половиной, 0.2.
NEED = NeedFacts(
    skills=(
        NeedSkill(skill_id=1, name="A", required=True, min_level=3, weight=0.8),
        NeedSkill(skill_id=2, name="B", required=False, min_level=2, weight=0.4),
    ),
    grade_position=1,
    grade_name="Middle",
    salary_min=200,
    salary_max=300,
    currency="RUB",
)


def candidate(**fields) -> CandidateFacts:
    base = dict(
        skills={},
        grade_position=1,
        grade_name="Middle",
        grade_confirmed=True,
        salary_min=None,
        salary_max=None,
        currency=None,
        strength_percentile=None,
        fsp=(),
        refreshed_at=None,
    )
    return CandidateFacts(**{**base, **fields})


def value(name: str, cand: CandidateFacts, need: NeedFacts = NEED) -> float:
    return compute_factors(need, cand, now=NOW)[name].value


def test_stack_confirmed_meets_level_counts_full_claimed_half_missing_zero() -> None:
    confirmed = candidate(skills={1: CandidateSkill(self_level=4, confirmed_level=4)})
    assert value("stack", confirmed) == pytest.approx(0.8 / 1.0)
    claimed = candidate(skills={1: CandidateSkill(self_level=4, confirmed_level=None)})
    assert value("stack", claimed) == pytest.approx(0.5 * 0.8 / 1.0)
    nice_only = candidate(skills={2: CandidateSkill(self_level=2, confirmed_level=2)})
    assert value("stack", nice_only) == pytest.approx(0.2 / 1.0)
    assert value("stack", candidate()) == 0.0


def test_stack_level_below_minimum_is_not_a_match() -> None:
    low = candidate(skills={1: CandidateSkill(self_level=2, confirmed_level=2)})
    assert value("stack", low) == 0.0


def test_stack_without_relevance_weight_falls_back_to_equal_weights() -> None:
    need = NeedFacts(
        skills=(
            NeedSkill(skill_id=1, name="A", required=True, min_level=None, weight=None),
            NeedSkill(skill_id=2, name="B", required=True, min_level=None, weight=None),
        ),
        grade_position=None,
        grade_name=None,
        salary_min=None,
        salary_max=None,
        currency="RUB",
    )
    cand = candidate(skills={1: CandidateSkill(self_level=0, confirmed_level=1)})
    assert value("stack", cand, need) == pytest.approx(0.5)


def test_grade_distance_and_neutral_without_grade() -> None:
    assert value("grade", candidate(grade_position=1)) == 1.0
    assert value("grade", candidate(grade_position=2)) == 0.5
    assert value("grade", candidate(grade_position=0)) == 0.5
    assert value("grade", candidate(grade_position=3)) == 0.0
    assert value("grade", candidate(grade_position=None, grade_name=None)) == NEUTRAL


def test_salary_inside_partial_and_missing() -> None:
    inside = candidate(salary_min=220, salary_max=280, currency="RUB")
    assert value("salary", inside) == 1.0
    partial = candidate(salary_min=250, salary_max=350, currency="RUB")
    assert value("salary", partial) == pytest.approx(0.5)
    assert value("salary", candidate()) == NEUTRAL
    other_currency = candidate(salary_min=1, salary_max=2, currency="USD")
    assert value("salary", other_currency) == NEUTRAL


def test_percentile_fsp_and_freshness() -> None:
    assert value("percentile", candidate(strength_percentile=0.75)) == 0.75
    assert value("percentile", candidate()) == NEUTRAL
    winner = candidate(fsp=(FspResult("Кубок", place=1, participants=180),))
    assert value("fsp", winner) == 1.0
    assert value("fsp", candidate()) == NEUTRAL
    middle = candidate(fsp=(FspResult("Кубок", place=33, participants=65),))
    assert NEUTRAL < value("fsp", middle) < 1.0
    assert value("freshness", candidate(refreshed_at=NOW - timedelta(days=3))) == 1.0
    assert value("freshness", candidate(refreshed_at=NOW - timedelta(days=400))) == 0.0
    assert value("freshness", candidate()) == NEUTRAL


def test_applicability_is_decided_by_the_query() -> None:
    bare = NeedFacts(
        skills=(),
        grade_position=None,
        grade_name=None,
        salary_min=None,
        salary_max=None,
        currency="RUB",
    )
    factors = compute_factors(bare, candidate(), now=NOW)
    assert not factors["stack"].applicable
    assert not factors["grade"].applicable
    assert not factors["salary"].applicable
    assert factors["percentile"].applicable and factors["freshness"].applicable
    full = compute_factors(NEED, candidate(), now=NOW)
    assert all(factor.applicable for factor in full.values())


def test_factor_score_is_mean_of_applicable_factors() -> None:
    bare = NeedFacts(
        skills=(),
        grade_position=None,
        grade_name=None,
        salary_min=None,
        salary_max=None,
        currency="RUB",
    )
    cand = candidate(
        strength_percentile=1.0, refreshed_at=NOW, fsp=(FspResult("К", place=1, participants=2),)
    )
    factors = compute_factors(bare, cand, now=NOW)
    assert factor_score(factors) == pytest.approx(1.0)


def test_every_factor_has_an_explanation_line() -> None:
    for cand in (candidate(), candidate(skills={1: CandidateSkill(4, 4)}, salary_min=1)):
        for name, factor in compute_factors(NEED, cand, now=NOW).items():
            assert factor.line.strip(), name
            assert 0.0 <= factor.value <= 1.0, name
