import pytest

from taskgen.config import ScoringSettings
from taskgen.contracts import CaseOutcome, TestReport
from taskgen.evaluation.scoring import (
    Components,
    code_task_score,
    performance_score,
    resolve_weights,
)

WEIGHTS = {
    "weight_tests": 0.6,
    "weight_rubric": 0.25,
    "weight_quality": 0.1,
    "weight_performance": 0.05,
}


def test_score_is_weighted_sum_of_components():
    components = Components(tests=1.0, rubric=0.5, quality=0.2, performance=1.0)
    expected = 0.6 * 1.0 + 0.25 * 0.5 + 0.1 * 0.2 + 0.05 * 1.0
    assert code_task_score(components, WEIGHTS) == pytest.approx(expected)


def test_zero_pass_rate_gates_score_to_zero_whatever_else():
    components = Components(tests=0.0, rubric=1.0, quality=1.0, performance=1.0)
    assert code_task_score(components, WEIGHTS) == 0.0


def test_small_nonzero_pass_rate_is_not_gated():
    components = Components(tests=0.1, rubric=1.0, quality=1.0, performance=1.0)
    assert code_task_score(components, WEIGHTS) == pytest.approx(0.06 + 0.4)


def test_missing_component_redistributes_its_weight():
    # Нет анализатора и эталонного отчёта: доступны только тесты и рубрика.
    components = Components(tests=1.0, rubric=0.0, quality=None, performance=None)
    assert code_task_score(components, WEIGHTS) == pytest.approx(0.6 / 0.85)


def test_perfect_components_give_one_even_with_missing_parts():
    components = Components(tests=1.0, rubric=1.0, quality=None, performance=None)
    assert code_task_score(components, WEIGHTS) == pytest.approx(1.0)


def test_template_override_replaces_only_named_weights():
    weights = resolve_weights(ScoringSettings(), {"weight_performance": 0.15})
    assert weights["weight_performance"] == 0.15
    assert weights["weight_tests"] == ScoringSettings().weight_tests


def test_template_override_ignores_keys_that_are_not_weights():
    weights = resolve_weights(ScoringSettings(), {"pass_threshold": 0.1})
    assert "pass_threshold" not in weights


def test_all_weights_zero_is_rejected():
    zero = {name: 0.0 for name in WEIGHTS}
    with pytest.raises(ValueError):
        code_task_score(Components(tests=1.0), zero)


def _report(walls: list[int | None], passed: list[bool], memory: int | None) -> TestReport:
    outcomes = [CaseOutcome(passed=p, wall_ms=w) for w, p in zip(walls, passed, strict=True)]
    return TestReport(outcomes=outcomes, max_memory_bytes=memory)


def test_performance_equal_to_reference_is_one():
    candidate = _report([100, 100], [True, True], 50_000_000)
    reference = _report([100, 100], [True, True], 50_000_000)
    assert performance_score(candidate, reference) == pytest.approx(1.0)


def test_performance_twice_slower_halves_time_part():
    candidate = _report([200, 200], [True, True], None)
    reference = _report([100, 100], [True, True], None)
    assert performance_score(candidate, reference) == pytest.approx(0.5)


def test_performance_faster_than_reference_is_capped_at_one():
    candidate = _report([10, 10], [True, True], None)
    reference = _report([100, 100], [True, True], None)
    assert performance_score(candidate, reference, time_floor_ms=1) == pytest.approx(1.0)


def test_performance_counts_only_cases_the_candidate_passed():
    # Упавший мгновенно случай не должен делать решение "быстрым".
    candidate = _report([400, 1], [True, False], None)
    reference = _report([100, 100], [True, True], None)
    assert performance_score(candidate, reference) == pytest.approx(0.25)


def test_performance_noise_floor_hides_tiny_differences():
    candidate = _report([4], [True], None)
    reference = _report([1], [True], None)
    assert performance_score(candidate, reference, time_floor_ms=20) == pytest.approx(1.0)


def test_performance_averages_time_and_memory():
    candidate = _report([100], [True], 200_000_000)
    reference = _report([100], [True], 50_000_000)
    assert performance_score(candidate, reference) == pytest.approx((1.0 + 0.25) / 2)


def test_performance_without_reference_is_unknown():
    candidate = _report([100], [True], 1)
    assert performance_score(candidate, None) is None
