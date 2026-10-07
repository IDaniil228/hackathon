import json
import re
import uuid

import pytest

from taskgen.config import ScoringSettings
from taskgen.contracts import AnalysisReport, CaseOutcome, HiddenPayload, TaskKind, TestReport
from taskgen.evaluation.judge import LLMJudge
from taskgen.evaluation.service import AnswerInput, TaskInput, evaluate_task
from tests.evaluation.fakes import QUOTES, FakeLLM

CODE = "def add(a: int, b: int) -> int:\n    return a + b\n"
RUBRIC = [{"id": "uses_plus", "text": "Складывает числа оператором +"}]


def judge_responder(verdict: str = "yes"):
    """Судья, который цитирует первую строку ответа кандидата."""

    def respond(messages, schema):
        user = messages[-1].content
        answer = re.search(r"<<<ОТВЕТ-\w+>>>\n(.*?)\n<<<КОНЕЦ", user, re.DOTALL).group(1)
        quote = next(line for line in answer.splitlines() if line.strip())
        ids = schema["properties"]["items"]["items"]["properties"]["item_id"]["enum"]
        items = [
            {"item_id": item_id, "verdict": verdict, "quotes": [quote] if verdict != "no" else []}
            for item_id in ids
        ]
        return json.dumps({"items": items}, ensure_ascii=False)

    return respond


def _report(passed: list[bool], wall: int = 100) -> dict:
    outcomes = [CaseOutcome(passed=p, wall_ms=wall) for p in passed]
    return TestReport(outcomes=outcomes).model_dump()


def _task(**overrides) -> TaskInput:
    hidden = {
        "cases": [{"input": [1, 2], "expected": 3}],
        "io": "function",
        "entrypoint": "add",
        "runtime": "python",
        "check": {"mode": "json"},
        "rubric": RUBRIC,
        "reference_report": _report([True, True]),
    }
    hidden.update(overrides.pop("hidden", {}))
    answer = AnswerInput(
        content=CODE,
        language="python",
        test_report=_report([True, True]),
        analysis=[AnalysisReport(tool="radon", metrics={"cc_max": 1, "mi": 100}).model_dump()],
        behavior={},
    )
    defaults = {
        "task_id": uuid.uuid4(),
        "position": 1,
        "kind": TaskKind.CODE,
        "grade": "junior",
        "statement": "Сложите два числа",
        "hidden": HiddenPayload.model_validate(hidden),
        "answer": answer,
    }
    defaults.update(overrides)
    return TaskInput(**defaults)


def _judge(verdict: str = "yes") -> tuple[LLMJudge, FakeLLM]:
    llm = FakeLLM(judge_responder(verdict))
    return LLMJudge(llm, model="j", samples=3, temperature=0.7, evidence=QUOTES), llm


async def test_perfect_code_answer_scores_one():
    judge, _ = _judge()
    outcome = await evaluate_task(_task(), judge=judge, scoring=ScoringSettings())
    assert outcome.components.tests == 1.0
    assert outcome.components.quality == pytest.approx(1.0)
    assert outcome.components.performance == pytest.approx(1.0)
    assert outcome.score == pytest.approx(1.0)


async def test_gate_skips_judge_and_scores_zero():
    judge, llm = _judge()
    task = _task()
    task.answer.test_report = _report([False, False])
    outcome = await evaluate_task(task, judge=judge, scoring=ScoringSettings())
    assert outcome.score == 0.0
    assert llm.calls == []


async def test_missing_test_report_counts_as_no_tests_passed():
    judge, _ = _judge()
    task = _task()
    task.answer.test_report = None
    outcome = await evaluate_task(task, judge=judge, scoring=ScoringSettings())
    assert outcome.score == 0.0


GROUPED = {
    "cases": [
        {"input": [1, 2], "expected": 3, "group": "manual"},
        {"input": [2, 2], "expected": 4, "group": "manual"},
        {"input": [10**6, 1], "expected": 10**6 + 1, "group": "stress"},
    ],
    "case_groups": {"manual": {"weight": 1}, "stress": {"weight": 3}},
}


async def test_tests_component_is_weighted_by_case_groups():
    judge, _ = _judge()
    task = _task(hidden=GROUPED)
    task.answer.test_report = _report([True, True, False])
    outcome = await evaluate_task(task, judge=judge, scoring=ScoringSettings())
    # Две трети кейсов пройдены, но стресс-группа весит 3 из 4.
    assert outcome.components.tests == pytest.approx(0.25)
    assert (outcome.tests.passed, outcome.tests.total) == (2, 3)


async def test_required_group_modes_reach_the_outcome():
    judge, _ = _judge()
    required = GROUPED | {
        "case_groups": {"manual": {"weight": 1}, "stress": {"weight": 3, "required": True}}
    }
    modes = {
        "выключен": (GROUPED, ScoringSettings()),
        "шаблон": (required, ScoringSettings()),
        "настройка": (GROUPED, ScoringSettings(required_case_groups=["stress"])),
    }
    failed = {}
    for mode, (hidden, scoring) in modes.items():
        task = _task(hidden=hidden)
        task.answer.test_report = _report([True, True, False])
        outcome = await evaluate_task(task, judge=judge, scoring=scoring)
        failed[mode] = outcome.tests.failed_required
        # Гейт не трогает балл: веса работают и без него.
        assert outcome.components.tests == pytest.approx(0.25)
    assert failed == {"выключен": [], "шаблон": ["stress"], "настройка": ["stress"]}


async def test_template_weights_override_applies():
    judge, _ = _judge("no")
    plain = await evaluate_task(_task(), judge=judge, scoring=ScoringSettings())
    heavy_rubric = await evaluate_task(
        _task(hidden={"scoring": {"weight_rubric": 10.0}}), judge=judge, scoring=ScoringSettings()
    )
    assert heavy_rubric.score < plain.score


async def test_judge_receives_test_and_metric_facts():
    judge, llm = _judge()
    await evaluate_task(_task(), judge=judge, scoring=ScoringSettings())
    user = llm.calls[0]["messages"][-1].content
    assert "radon.cc_max" in user
    assert "цикломатическая сложность" in user  # трактовка из рубрики метрик
    assert '"пройдено": 2' in user


async def test_essay_score_is_judge_score():
    judge, _ = _judge("partial")
    task = _task(kind=TaskKind.ESSAY)
    task.answer.test_report = None
    task.answer.analysis = None
    task.answer.content = "Индекс ускоряет поиск, но замедляет вставку."
    outcome = await evaluate_task(task, judge=judge, scoring=ScoringSettings())
    assert outcome.score == pytest.approx(0.5)


async def test_no_answer_scores_zero_without_judge():
    judge, llm = _judge()
    outcome = await evaluate_task(_task(answer=None), judge=judge, scoring=ScoringSettings())
    assert outcome.score == 0.0
    assert not outcome.answered
    assert llm.calls == []


async def test_code_without_rubric_redistributes_rubric_weight():
    judge, llm = _judge()
    outcome = await evaluate_task(
        _task(hidden={"rubric": []}), judge=judge, scoring=ScoringSettings()
    )
    assert outcome.components.rubric is None
    assert outcome.score == pytest.approx(1.0)
    assert llm.calls == []
