"""Грейд по уровням: чистая функция от баллов заданий.

В оценку по уровням входят только задания с кодом и эссе. Тестовая задачка проверяет знание
одного факта, и угаданный ответ получает полный балл с вероятностью 1/4: такое задание не
доказывает уровень и не должно ни подтверждать его, ни снимать.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from taskgen.contracts import TaskKind

Outcome = Literal["passed", "failed", "upgraded", "downgraded", "insufficient"]

GRADED_KINDS = frozenset({TaskKind.CODE, TaskKind.ESSAY})


class GradeLevel(BaseModel):
    code: str
    level: int


class TaskScore(BaseModel):
    grade: str
    score: float
    kind: TaskKind = TaskKind.CODE
    # False — упал кейс обязательной группы, и задание не решено при любом балле.
    required_passed: bool = True


class LevelResult(BaseModel):
    grade: str
    total: int
    solved: int
    share: float
    share_passed: bool
    confirmed: bool


class Recommendation(BaseModel):
    action: Literal["none", "take_test"]
    grade: str | None = None
    text: str


class GradeAssessment(BaseModel):
    levels: list[LevelResult]
    claimed: str
    assigned: str | None
    outcome: Outcome
    recommendation: Recommendation


# Баллы — суммы произведений float, и 0.7 может прийти как 0.6999999999999999.
_EPSILON = 1e-9


def _at_least(value: float, threshold: float) -> bool:
    return value >= threshold - _EPSILON


def is_solved(score: float, threshold: float, *, required_passed: bool = True) -> bool:
    """Решено задание: балл не ниже порога и пройдены все кейсы обязательных групп."""
    return required_passed and _at_least(score, threshold)


def assess_grade(
    tasks: list[TaskScore],
    grades: list[GradeLevel],
    claimed: str,
    *,
    solved_threshold: float,
    pass_threshold: float,
    min_evidence: int,
) -> GradeAssessment:
    """Присваивает старший уровень, который подтверждён сам и подкреплён младшими.

    Задания kind=quiz не учитываются. Если заданий заявленного уровня меньше min_evidence,
    вердикт — insufficient: судить о заявленном грейде не по чему.

    Уровень подтверждён, если доля решённых заданий уровня не меньше pass_threshold и доля
    решённых среди всех заданий представленных младших уровней тоже не меньше порога.
    Младшие уровни проверяются вместе, а не каждый отдельно: на уровень приходится одно-два
    задания, и при проверке каждого одна ошибка на младшем уровне стирала бы все старшие.
    Требование к младшим остаётся: решённый старший уровень при проваленных младших —
    скорее удачная догадка, чем грейд.
    """
    ordered = sorted(grades, key=lambda grade: grade.level)
    position = {grade.code: index for index, grade in enumerate(ordered)}
    unknown = {task.grade for task in tasks} - position.keys()
    if unknown or claimed not in position:
        raise ValueError(f"Грейды не найдены в справочнике: {sorted(unknown | {claimed})}")
    tasks = [task for task in tasks if task.kind in GRADED_KINDS]

    levels: list[LevelResult] = []
    lower_total = lower_solved = 0
    for grade in ordered:
        level_tasks = [task for task in tasks if task.grade == grade.code]
        if not level_tasks:
            continue
        solved = sum(
            is_solved(task.score, solved_threshold, required_passed=task.required_passed)
            for task in level_tasks
        )
        share = solved / len(level_tasks)
        share_passed = _at_least(share, pass_threshold)
        lower_ok = lower_total == 0 or _at_least(lower_solved / lower_total, pass_threshold)
        levels.append(
            LevelResult(
                grade=grade.code,
                total=len(level_tasks),
                solved=solved,
                share=share,
                share_passed=share_passed,
                confirmed=share_passed and lower_ok,
            )
        )
        lower_total += len(level_tasks)
        lower_solved += solved

    evidence = sum(task.grade == claimed for task in tasks)
    if evidence < min_evidence:
        return GradeAssessment(
            levels=levels,
            claimed=claimed,
            assigned=None,
            outcome="insufficient",
            recommendation=Recommendation(
                action="take_test",
                grade=claimed,
                text=(
                    f"На заявленном уровне {claimed} заданий с кодом и эссе {evidence}, а для "
                    f"вердикта нужно не меньше {min_evidence}: грейд не оценён. Рекомендуется "
                    f"пройти тест на грейд {claimed} ещё раз"
                ),
            ),
        )

    confirmed = [level.grade for level in levels if level.confirmed]
    assigned = confirmed[-1] if confirmed else None
    outcome = _outcome(assigned, claimed, position)
    return GradeAssessment(
        levels=levels,
        claimed=claimed,
        assigned=assigned,
        outcome=outcome,
        recommendation=_recommend(outcome, assigned, claimed, ordered),
    )


def _outcome(assigned: str | None, claimed: str, position: dict[str, int]) -> Outcome:
    if assigned is None:
        return "failed"
    if position[assigned] == position[claimed]:
        return "passed"
    return "upgraded" if position[assigned] > position[claimed] else "downgraded"


def _recommend(
    outcome: str, assigned: str | None, claimed: str, ordered: list[GradeLevel]
) -> Recommendation:
    if outcome == "passed":
        return Recommendation(action="none", text=f"Заявленный грейд {claimed} подтверждён")
    if assigned is not None:
        # В тесте на заявленный грейд заданий другого уровня мало, поэтому подтверждённый
        # уровень стоит проверить полным тестом на этот уровень.
        relation = "выше" if outcome == "upgraded" else "ниже"
        return Recommendation(
            action="take_test",
            grade=assigned,
            text=(
                f"Подтверждён грейд {assigned}, {relation} заявленного {claimed}. "
                f"Рекомендуется пройти тест на грейд {assigned}"
            ),
        )
    codes = [grade.code for grade in ordered]
    index = codes.index(claimed)
    target = codes[index - 1] if index > 0 else claimed
    return Recommendation(
        action="take_test",
        grade=target,
        text=f"Ни один уровень не подтверждён. Рекомендуется пройти тест на грейд {target}",
    )
