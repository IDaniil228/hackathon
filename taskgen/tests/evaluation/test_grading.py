import pytest

from taskgen.evaluation.grading import GradeLevel, TaskScore, assess_grade

GRADES = [
    GradeLevel(code="senior", level=30),
    GradeLevel(code="junior", level=10),
    GradeLevel(code="middle", level=20),
]
SOLVED = 0.7
PASS = 0.6


def _assess(scores: list[tuple[str, float]], claimed: str):
    tasks = [TaskScore(grade=grade, score=score) for grade, score in scores]
    return assess_grade(tasks, GRADES, claimed, solved_threshold=SOLVED, pass_threshold=PASS)


def test_all_levels_solved_assigns_highest_represented():
    result = _assess([("junior", 1.0), ("middle", 1.0), ("senior", 0.9)], "middle")
    assert result.assigned == "senior"
    assert result.outcome == "upgraded"


def test_claimed_level_solved_but_higher_failed_is_passed():
    result = _assess([("junior", 0.9), ("middle", 0.8), ("middle", 0.8), ("senior", 0.1)], "middle")
    assert result.assigned == "middle"
    assert result.outcome == "passed"
    assert result.recommendation.action == "none"


def test_order_follows_level_not_list_order_or_name():
    # Список грейдов перемешан; порядок задаёт level.
    result = _assess([("senior", 1.0), ("junior", 0.0), ("middle", 1.0)], "senior")
    levels = [item.grade for item in result.levels]
    assert levels == ["junior", "middle", "senior"]


def test_score_exactly_at_solved_threshold_counts_as_solved():
    result = _assess([("junior", SOLVED)], "junior")
    assert result.levels[0].solved == 1


def test_share_exactly_at_pass_threshold_passes_level():
    scores = [("junior", 1.0)] * 3 + [("junior", 0.0)] * 2
    result = _assess(scores, "junior")
    assert result.levels[0].share == pytest.approx(0.6)
    assert result.assigned == "junior"


def test_upper_level_without_lower_support_is_not_confirmed():
    # Старший уровень решён, а оба младших провалены: удачная догадка, а не грейд.
    result = _assess([("junior", 0.0), ("middle", 0.0), ("senior", 1.0)], "senior")
    senior = result.levels[-1]
    assert senior.share_passed and not senior.confirmed
    assert result.assigned is None
    assert result.outcome == "failed"


def test_one_slip_on_a_lower_level_does_not_erase_upper_levels():
    # Младшие уровни проверяются вместе: 3 из 4 младших заданий — это 0.75 >= 0.6.
    scores = [("junior", 1.0), ("junior", 0.0), ("middle", 1.0), ("middle", 1.0), ("senior", 1.0)]
    result = _assess(scores, "senior")
    assert result.assigned == "senior"
    assert result.outcome == "passed"


def test_failed_claimed_level_with_confirmed_lower_is_downgraded():
    result = _assess([("junior", 1.0), ("middle", 0.2), ("middle", 0.3)], "middle")
    assert result.assigned == "junior"
    assert result.outcome == "downgraded"
    assert result.recommendation.action == "take_test"
    assert result.recommendation.grade == "junior"


def test_nothing_confirmed_recommends_one_grade_below_claimed():
    result = _assess([("middle", 0.0), ("middle", 0.1)], "middle")
    assert result.assigned is None
    assert result.outcome == "failed"
    assert result.recommendation.grade == "junior"


def test_nothing_confirmed_on_lowest_grade_recommends_same_grade():
    result = _assess([("junior", 0.0)], "junior")
    assert result.recommendation.grade == "junior"


def test_upgraded_recommends_full_test_on_assigned_grade():
    result = _assess([("junior", 1.0), ("senior", 1.0)], "junior")
    assert result.outcome == "upgraded"
    assert result.recommendation.grade == "senior"


def test_no_tasks_is_failed():
    assert _assess([], "junior").outcome == "failed"


def test_unknown_grade_is_rejected():
    with pytest.raises(ValueError):
        _assess([("lead", 1.0)], "junior")
    with pytest.raises(ValueError):
        _assess([("junior", 1.0)], "lead")
