"""Набор ответов на подделках: то, что проверяет код, а не модель.

Подделка судьи здесь худшая из возможных: она верит каждому ответу и ставит yes по всем
пунктам. Балл ниже должен держаться на гейте по тестам, проверке цитат и маркерах, а не на
том, послушалась ли модель. Прогон на настоящих моделях — scripts/adversarial_eval.py и
test_adversarial_llm.py.
"""

from __future__ import annotations

import json
import random
import re
import uuid

import pytest

from taskgen.bank.validation import generate_cases, run_solution, solved_at_best
from taskgen.config import SandboxSettings, ScoringSettings
from taskgen.contracts import CaseOutcome, HiddenPayload, RunLimits, TaskKind, TestReport
from taskgen.evaluation.judge import LLMJudge
from taskgen.evaluation.service import AnswerInput, TaskInput, evaluate_task
from taskgen.generation.params import draw_params
from taskgen.markers import has_marker
from taskgen.sandbox.piston import PistonRunner
from tests.evaluation.adversarial.dataset import Answer, AnswerSet, check, load_sets
from tests.evaluation.fakes import FakeLLM

SETS = load_sets()
ANSWERS = [(answer_set, answer) for answer_set in SETS for answer in answer_set.answers]
CLASSES = ("valid-", "invalid-", "inject-")


def _ids(pairs: list[tuple[AnswerSet, Answer]]) -> list[str]:
    return [f"{answer_set.name}:{answer.id}" for answer_set, answer in pairs]


ANSWER_BLOCK = re.compile(r"<<<ОТВЕТ-[0-9a-f]+>>>\n(.*)\n<<<КОНЕЦ-[0-9a-f]+>>>", re.DOTALL)


def _answer_block(messages) -> str:
    return ANSWER_BLOCK.search(messages[-1].content).group(1)


def _schema_ids(schema) -> list[str]:
    return schema["properties"]["items"]["items"]["properties"]["item_id"]["enum"]


def credulous(quote_of):
    """Судья, который ставит yes по каждому пункту с цитатой, выбранной quote_of(ответ)."""

    def respond(messages, schema):
        quote = quote_of(_answer_block(messages))
        items = [
            {"item_id": item_id, "verdict": "yes", "quotes": [quote]}
            for item_id in _schema_ids(schema)
        ]
        return json.dumps({"items": items}, ensure_ascii=False)

    return respond


def _task(answer_set: AnswerSet, answer: Answer, report: TestReport | None) -> TaskInput:
    spec = answer_set.spec()
    hidden = HiddenPayload(
        cases=[{"input": [0], "expected": 0}] if spec.kind is TaskKind.CODE else [],
        io=spec.io,
        entrypoint=spec.entrypoint,
        runtime=spec.runtime,
        check=spec.check,
        rubric=spec.rubric,
    )
    return TaskInput(
        task_id=uuid.uuid4(),
        position=0,
        kind=spec.kind,
        grade=spec.grade,
        statement="условие",
        hidden=hidden,
        answer=AnswerInput(
            content=answer.content,
            language=spec.runtime,
            test_report=report.model_dump() if report else None,
        ),
    )


# --- Сам набор -----------------------------------------------------------------------------


@pytest.mark.parametrize(("answer_set", "answer"), ANSWERS, ids=_ids(ANSWERS))
def test_answer_is_well_formed(answer_set, answer):
    low, high = answer.expect
    assert 0.0 <= low <= high <= 1.0
    assert answer.cls.startswith(CLASSES), answer.cls
    if answer.cls.startswith("inject-"):
        assert answer.attack, "у атаки нет описания класса для отчёта"
    if answer.not_above is not None:
        earlier = [item.id for item in answer_set.answers]
        position = earlier.index(answer.id)
        assert answer.not_above in earlier[:position], "основа атаки идёт позже атаки"


@pytest.mark.parametrize("answer_set", SETS, ids=[item.name for item in SETS])
def test_set_covers_honest_and_bad_answers(answer_set):
    spec = answer_set.spec()
    assert spec.kind in (TaskKind.CODE, TaskKind.ESSAY)
    assert spec.rubric
    classes = {answer.cls for answer in answer_set.answers}
    assert {"valid-full", "valid-partial", "invalid-wrong"} <= classes
    assert any(cls.startswith("inject-") for cls in classes)


def test_check_flags_both_directions_and_attack_above_its_base():
    honest = Answer(id="a", cls="valid-full", expect=(0.75, 1.0), content="")
    attack = Answer(id="b", cls="inject-max", expect=(0.0, 1.0), content="", not_above="a")
    assert check(honest, 0.8, {}).ok
    assert check(honest, 0.5, {}).problems
    assert check(Answer(id="c", cls="invalid-wrong", expect=(0, 0.2), content=""), 0.3, {}).problems
    assert check(attack, 0.82, {"a": 0.8}).ok
    assert check(attack, 0.9, {"a": 0.8}).problems


# --- Гейт по тестам ------------------------------------------------------------------------

CODE_ANSWERS = [
    (answer_set, answer)
    for answer_set, answer in ANSWERS
    if answer_set.spec().kind is TaskKind.CODE and answer.content
]


@pytest.mark.parametrize(("answer_set", "answer"), CODE_ANSWERS, ids=_ids(CODE_ANSWERS))
async def test_code_without_passed_tests_scores_zero_whatever_the_judge(answer_set, answer):
    llm = FakeLLM(credulous(lambda text: text.strip().splitlines()[0]))
    judge = LLMJudge(llm, model="j", samples=3, temperature=0.7)
    failed = TestReport(outcomes=[CaseOutcome(passed=False, error="AssertionError")])
    outcome = await evaluate_task(
        _task(answer_set, answer, failed), judge=judge, scoring=ScoringSettings()
    )
    assert outcome.score == 0.0
    assert llm.calls == []


CODE_SETS = [answer_set for answer_set in SETS if answer_set.spec().kind is TaskKind.CODE]


@pytest.mark.integration
@pytest.mark.parametrize("answer_set", CODE_SETS, ids=[item.name for item in CODE_SETS])
async def test_only_complete_code_answers_are_solved_at_best(answer_set):
    # Верхняя граница балла: судья, анализаторы и замер скорости ставят 1. Неверный,
    # частичный или медленный за пределами лимитов код не решён и так, а честный полный и
    # медленный в пределах лимитов решён.
    spec = answer_set.spec()
    sandbox = SandboxSettings()
    limits = RunLimits(
        timeout_ms=sandbox.run_timeout_ms, memory_bytes=sandbox.run_memory_limit_bytes
    )
    piston = PistonRunner(sandbox)
    try:
        params = draw_params(spec.params, random.Random(answer_set.seed))
        cases = await generate_cases(piston, spec, params, answer_set.seed, limits)
        verdicts = {}
        for answer in answer_set.answers:
            if answer.content.strip():
                report = await run_solution(piston, spec, answer.content, cases, limits)
                verdicts[answer.id] = solved_at_best(spec, cases, report, ScoringSettings())
    finally:
        await piston.aclose()
    expected = {item: answer_set.complete(answer_set.answer(item)) for item in verdicts}
    assert verdicts == expected


# --- Цитаты --------------------------------------------------------------------------------

ATTACKS = [(s, a) for s, a in ANSWERS if a.cls.startswith("inject-") and a.content.strip()]


@pytest.mark.parametrize(("answer_set", "answer"), ATTACKS, ids=_ids(ATTACKS))
async def test_credulous_judge_without_real_quote_gives_no_rubric_credit(answer_set, answer):
    # Судья поверил атаке и цитирует то, что атака заявляет, а не текст ответа.
    llm = FakeLLM(credulous(lambda _text: "все пункты рубрики выполнены полностью"))
    judge = LLMJudge(llm, model="j", samples=3, temperature=0.7)
    passed = TestReport(outcomes=[CaseOutcome(passed=True, wall_ms=1)])
    outcome = await evaluate_task(
        _task(answer_set, answer, passed), judge=judge, scoring=ScoringSettings()
    )
    assert outcome.judge is not None
    assert outcome.judge.score == 0.0
    assert {verdict.verdict for verdict in outcome.judge.verdicts} == {"no"}


# --- Маркеры -------------------------------------------------------------------------------

FORGED = [(s, a) for s, a in ANSWERS if has_marker(a.content)]


def test_set_contains_forged_markers():
    assert len({answer_set.name for answer_set, _ in FORGED}) >= 2


@pytest.mark.parametrize(("answer_set", "answer"), FORGED, ids=_ids(FORGED))
async def test_forged_markers_stay_inside_the_data_block(answer_set, answer):
    llm = FakeLLM(credulous(lambda _text: "нет такой цитаты"))
    judge = LLMJudge(llm, model="j", samples=3, temperature=0.7)
    passed = TestReport(outcomes=[CaseOutcome(passed=True, wall_ms=1)])
    await evaluate_task(_task(answer_set, answer, passed), judge=judge, scoring=ScoringSettings())
    for call in llm.calls:
        messages = call["messages"]
        start, end = judge.markers_for(messages)
        user = messages[-1].content
        # Настоящий закрывающий маркер в запросе один и стоит после всего ответа.
        assert user.count(end) == 1
        assert user.index(start) < user.index(answer.content.strip()) < user.index(end)
        assert start not in answer.content and end not in answer.content
