import pytest

from taskgen.contracts import TaskKind
from taskgen.evaluation.grading import GradeLevel, TaskScore, assess_grade, is_solved

GRADES = [
    GradeLevel(code="senior", level=30),
    GradeLevel(code="junior", level=10),
    GradeLevel(code="middle", level=20),
]
SOLVED = 0.7
PASS = 0.6


def _assess(scores: list[tuple[str, float]], claimed: str, *, min_evidence: int = 1, quiz=()):
    tasks = [TaskScore(grade=grade, score=score) for grade, score in scores]
    tasks += [TaskScore(grade=grade, score=score, kind=TaskKind.QUIZ) for grade, score in quiz]
    return assess_grade(
        tasks,
        GRADES,
        claimed,
        solved_threshold=SOLVED,
        pass_threshold=PASS,
        min_evidence=min_evidence,
    )


def test_claimed_level_without_enough_evidence_is_insufficient():
    # Одно решённое задание senior при нуле заданий middle — не повод для вердикта о middle.
    result = _assess([("junior", 1.0), ("senior", 1.0)], "middle")
    assert result.outcome == "insufficient"
    assert result.assigned is None
    assert result.recommendation.action == "take_test"
    assert result.recommendation.grade == "middle"
    assert "middle" in result.recommendation.text and "0" in result.recommendation.text
    # Уровни всё равно посчитаны: работодатель видит, что было в тесте.
    assert [level.grade for level in result.levels] == ["junior", "senior"]


def test_min_evidence_counts_tasks_of_claimed_level():
    scores = [("middle", 1.0), ("middle", 1.0)]
    assert _assess(scores, "middle", min_evidence=2).outcome == "passed"
    assert _assess(scores, "middle", min_evidence=3).outcome == "insufficient"


def test_quiz_tasks_do_not_take_part_in_levels_or_evidence():
    # Тестовые задачки middle решены, но в оценку по уровням не входят: доказательств нет.
    quiz = [("middle", 1.0)] * 5
    result = _assess([("junior", 1.0)], "middle", quiz=quiz)
    assert result.outcome == "insufficient"
    assert [level.grade for level in result.levels] == ["junior"]
    # И не портят вердикт: проваленные задачки не снимают подтверждённый уровень.
    failed_quiz = [("middle", 0.0)] * 5
    passed = _assess([("middle", 1.0)], "middle", quiz=failed_quiz)
    assert passed.outcome == "passed"
    assert [(level.grade, level.total) for level in passed.levels] == [("middle", 1)]


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


def test_order_follows_level_even_when_names_sort_otherwise():
    # trainee по алфавиту последний, но по level самый младший.
    grades = [*GRADES, GradeLevel(code="trainee", level=5)]
    tasks = [TaskScore(grade="trainee", score=1.0), TaskScore(grade="junior", score=1.0)]
    result = assess_grade(
        tasks, grades, "trainee", solved_threshold=SOLVED, pass_threshold=PASS, min_evidence=1
    )
    assert [item.grade for item in result.levels] == ["trainee", "junior"]
    assert result.assigned == "junior"
    assert result.outcome == "upgraded"


def test_score_exactly_at_solved_threshold_counts_as_solved():
    result = _assess([("junior", SOLVED)], "junior")
    assert result.levels[0].solved == 1


def test_failed_required_group_is_not_solved_whatever_the_score():
    tasks = [TaskScore(grade="junior", score=1.0, required_passed=False)]
    result = assess_grade(
        tasks, GRADES, "junior", solved_threshold=SOLVED, pass_threshold=PASS, min_evidence=1
    )
    assert result.levels[0].solved == 0
    assert result.outcome == "failed"


def test_is_solved_needs_score_and_required_groups():
    assert is_solved(SOLVED, SOLVED)
    assert not is_solved(1.0, SOLVED, required_passed=False)
    assert not is_solved(SOLVED - 0.01, SOLVED)


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


def test_no_tasks_is_insufficient_and_without_evidence_rule_failed():
    assert _assess([], "junior").outcome == "insufficient"
    assert _assess([], "junior", min_evidence=0).outcome == "failed"


def test_unknown_grade_is_rejected():
    with pytest.raises(ValueError):
        _assess([("lead", 1.0)], "junior")
    with pytest.raises(ValueError):
        _assess([("junior", 1.0)], "lead")
