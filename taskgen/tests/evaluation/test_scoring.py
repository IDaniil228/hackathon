import pytest

from taskgen.config import ScoringSettings
from taskgen.contracts import CaseGroup, CaseOutcome, TaskKind, TestCase, TestReport
from taskgen.evaluation.scoring import (
    Components,
    code_task_score,
    performance_score,
    quiz_score,
    resolve_weights,
    session_score,
    weighted_pass_rate,
)

CODE, ESSAY, QUIZ = TaskKind.CODE, TaskKind.ESSAY, TaskKind.QUIZ


def test_quiz_answer_scores_one_only_for_the_key():
    assert quiz_score("c", "c") == 1.0
    assert quiz_score("a", "c") == 0.0


def test_session_score_mixes_tasks_mean_and_quiz_share_by_weight():
    scored = [(CODE, 0.8), (ESSAY, 0.6), (QUIZ, 1.0), (QUIZ, 0.0), (QUIZ, 1.0), (QUIZ, 1.0)]
    result = session_score(scored, weight_quiz=0.1)
    assert result.tasks == pytest.approx(0.7)
    assert (result.quiz.correct, result.quiz.total) == (3, 4)
    assert result.quiz.share == pytest.approx(0.75)
    assert result.score == pytest.approx(0.9 * 0.7 + 0.1 * 0.75)
    assert result.weights == {"tasks": pytest.approx(0.9), "quiz": pytest.approx(0.1)}


def test_session_score_without_one_block_takes_the_other_whole():
    only_tasks = session_score([(CODE, 0.4)], weight_quiz=0.1)
    assert only_tasks.quiz is None and only_tasks.score == pytest.approx(0.4)
    only_quiz = session_score([(QUIZ, 1.0), (QUIZ, 0.0)], weight_quiz=0.1)
    assert only_quiz.tasks is None and only_quiz.score == pytest.approx(0.5)


def test_quiz_block_cannot_outweigh_failed_practical_tasks():
    # Все задачки верны, практические задания провалены: итог не выше веса задачек.
    result = session_score([(CODE, 0.0), (ESSAY, 0.0), *[(QUIZ, 1.0)] * 5], weight_quiz=0.1)
    assert result.score == pytest.approx(0.1)


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
    # Качество и скорость идут с множителем доли тестов: 0.1 · (0.1 + 0.05).
    assert code_task_score(components, WEIGHTS) == pytest.approx(0.06 + 0.25 + 0.015)


def test_quality_and_speed_are_scaled_by_tests_share():
    # Один кейс из 27 с идеальными качеством и скоростью: раньше они давали 0.15 балла.
    tests = 1 / 27
    components = Components(tests=tests, rubric=0.0, quality=1.0, performance=1.0)
    assert code_task_score(components, WEIGHTS) == pytest.approx(0.75 * tests)


def test_quality_scaling_keeps_full_score_reachable():
    components = Components(tests=1.0, rubric=0.0, quality=1.0, performance=None)
    assert code_task_score(components, WEIGHTS) == pytest.approx(0.7 / 0.95)


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


GROUPS = {"manual": CaseGroup(weight=1), "stress": CaseGroup(weight=3)}


def _cases(*groups: str | None) -> list[TestCase]:
    return [TestCase(input=[n], expected=n, group=group) for n, group in enumerate(groups)]


def _passed(*flags: bool) -> TestReport:
    return TestReport(outcomes=[CaseOutcome(passed=flag) for flag in flags])


def test_weighted_pass_rate_weighs_groups_not_cases():
    # Три ручных кейса из трёх и ни одного стресс-кейса: доля кейсов 0.75, но вес
    # стресс-группы 3 из 4 оставляет за тесты только 0.25.
    cases = _cases("manual", "manual", "manual", "stress")
    assert weighted_pass_rate(_passed(True, True, True, False), cases, GROUPS) == pytest.approx(
        0.25
    )
    assert weighted_pass_rate(_passed(False, False, False, True), cases, GROUPS) == pytest.approx(
        0.75
    )


def test_group_share_inside_group_is_share_of_its_cases():
    cases = _cases("manual", "manual", "stress", "stress")
    share = weighted_pass_rate(_passed(True, False, True, False), cases, GROUPS)
    assert share == pytest.approx((1 * 0.5 + 3 * 0.5) / 4)


def test_weighted_pass_rate_without_groups_is_pass_rate():
    report = _passed(True, False, True)
    assert weighted_pass_rate(report, _cases(None, None, None), {}) == pytest.approx(
        report.pass_rate
    )


def test_declared_group_without_cases_leaves_the_denominator():
    # Группа объявлена, но в этом экземпляре её кейсов нет: её вес не делает балл ниже.
    cases = _cases("manual", "manual")
    assert weighted_pass_rate(_passed(True, True), cases, GROUPS) == pytest.approx(1.0)


def test_weighted_pass_rate_needs_an_outcome_per_case():
    with pytest.raises(ValueError):
        weighted_pass_rate(_passed(True), _cases("manual", "stress"), GROUPS)


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
