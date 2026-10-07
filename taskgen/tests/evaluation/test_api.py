"""API оценки на настоящей БД с подделками LLM и песочницы."""

import json
import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from taskgen.app import create_app
from taskgen.contracts import AISignal, CaseOutcome, SessionStatus, TaskStatus, TestReport
from taskgen.db import Answer, CandidateSummary, Task, Template, TestSession
from taskgen.sandbox.errors import SandboxError
from tests.conftest import ADMIN_KEY
from tests.evaluation.fakes import FakeLLM, FakeSandbox

pytestmark = pytest.mark.integration

AUTH = {"Authorization": f"Bearer {ADMIN_KEY}"}
SECRET_EXPECTED = 987654321
CODE = "def add(a: int, b: int) -> int:\n    return a + b\n"
RUBRIC = [{"id": "uses_plus_operator", "text": "Складывает числа оператором +"}]
QUIZ_KEY = "c"
QUIZ_OPTIONS = [{"id": i, "text": f"Ответ {i}"} for i in ("a", "b", "c", "d")]


class Responder:
    """Судья цитирует первую строку ответа; summary отвечает текстом без опоры на числа."""

    def __init__(self, verdict: str = "yes") -> None:
        self.verdict = verdict
        self.judge_calls = 0

    def __call__(self, messages, schema):
        properties = schema["properties"] if schema else {}
        if "sentences" in properties or (schema is None and "справку" in messages[0].content):
            return json.dumps({"sentences": ["Кандидат молодец."]}, ensure_ascii=False)
        self.judge_calls += 1
        # Провайдер без json_schema получает схему последним сообщением, после промпта судьи.
        user = "\n".join(message.content for message in messages)
        answer = re.search(r"<<<ОТВЕТ-\w+>>>\n(.*?)\n<<<КОНЕЦ", user, re.DOTALL).group(1)
        quote = next(line for line in answer.splitlines() if line.strip())
        item = properties["items"]["items"]["properties"]
        ids = item["item_id"]["enum"]
        verdict = self.verdict
        # Первый участок ответа — его первая непустая строка.
        evidence = {"spans": [1]} if "spans" in item else {"quotes": [quote]}
        items = [
            {"item_id": i, "verdict": verdict, **(evidence if verdict != "no" else {})} for i in ids
        ]
        return json.dumps({"items": items}, ensure_ascii=False)


def _report(passed: list[bool]) -> TestReport:
    return TestReport(outcomes=[CaseOutcome(passed=p, wall_ms=50) for p in passed])


class World:
    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.candidate = f"cand-{uuid.uuid4().hex[:8]}"
        self.sessions: list[uuid.UUID] = []
        self.templates: list[uuid.UUID] = []

    @property
    def state(self):
        return self.client.app.state

    def _run(self, fn, *args):
        return self.client.portal.call(fn, *args)

    def make_session(
        self,
        tasks: list[dict],
        claimed: str = "middle",
        status: SessionStatus = SessionStatus.READY,
    ) -> tuple[uuid.UUID, list]:
        async def seed():
            async with self.state.sessionmaker() as db, db.begin():
                session = TestSession(
                    candidate_ref=self.candidate,
                    area_code="algo",
                    claimed_grade_code=claimed,
                    skills=["python"],
                    temperature=0.3,
                    status=status,
                )
                db.add(session)
                await db.flush()
                task_ids = []
                for position, spec in enumerate(tasks, start=1):
                    template = Template(
                        name=f"t-{uuid.uuid4().hex}",
                        version=1,
                        area_code="algo",
                        grade_code=spec["grade"],
                        kind=spec["kind"],
                        runtime="python" if spec["kind"] == "code" else None,
                        difficulty=3,
                        skills=["python"],
                        description="тест",
                        body="тест",
                        content_hash="0" * 64,
                    )
                    db.add(template)
                    await db.flush()
                    self.templates.append(template.id)
                    hidden = {"rubric": RUBRIC}
                    if spec["kind"] == "code":
                        hidden |= {
                            "cases": [{"input": [1, 2], "expected": SECRET_EXPECTED}],
                            "io": "function",
                            "entrypoint": "add",
                            "runtime": "python",
                            "check": {"mode": "json"},
                            "reference_report": _report([True]).model_dump(),
                        } | spec.get("hidden", {})
                    public = {"signature": "def add(a, b)"}
                    if spec["kind"] == "quiz":
                        hidden = {"answer_key": QUIZ_KEY, "quiz_variant": "positive"}
                        public = {"title": "Задачка", "options": QUIZ_OPTIONS}
                    task = Task(
                        session_id=session.id,
                        template_id=template.id,
                        position=position,
                        status=TaskStatus.READY,
                        kind=spec["kind"],
                        statement="Сложите два числа",
                        params={},
                        seed=1,
                        public_payload=public,
                        hidden_payload=hidden,
                        generated_by="test",
                    )
                    db.add(task)
                    await db.flush()
                    task_ids.append(task.id)
                self.sessions.append(session.id)
                return session.id, task_ids

        return self._run(seed)

    def answers(self, task_id: uuid.UUID) -> list[Answer]:
        async def load():
            async with self.state.sessionmaker() as db:
                return (await db.scalars(select(Answer).where(Answer.task_id == task_id))).all()

        return self._run(load)

    def summary(self) -> CandidateSummary | None:
        async def load():
            async with self.state.sessionmaker() as db:
                return await db.get(CandidateSummary, self.candidate)

        return self._run(load)

    def cleanup(self) -> None:
        async def drop():
            async with self.state.sessionmaker() as db, db.begin():
                await db.execute(delete(TestSession).where(TestSession.id.in_(self.sessions)))
                await db.execute(delete(Template).where(Template.id.in_(self.templates)))
                await db.execute(
                    delete(CandidateSummary).where(CandidateSummary.candidate_ref == self.candidate)
                )

        self._run(drop)


@pytest.fixture
def world(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        client.portal.call(client.app.state.llm.aclose)
        client.app.state.responder = Responder()
        client.app.state.llm = FakeLLM(client.app.state.responder)
        client.app.state.sandbox = FakeSandbox(_report([True, True]))
        w = World(client)
        try:
            yield w
        finally:
            w.cleanup()


def _answer(world: World, task_id, **body):
    payload = {"content": CODE, "language": "python", "paste_count": 2} | body
    return world.client.post(f"/tasks/{task_id}/answer", json=payload, headers=AUTH)


def test_requires_key(world):
    assert world.client.post(f"/sessions/{uuid.uuid4()}/score").status_code == 401


def test_submit_runs_hidden_tests_and_stores_analysis(world):
    _, (task_id,) = world.make_session([{"grade": "middle", "kind": "code"}])
    response = _answer(world, task_id, focus_lost_count=1, time_spent_sec=120)
    assert response.status_code == 200, response.text
    assert str(SECRET_EXPECTED) not in response.text
    request = world.state.sandbox.requests[0]
    assert request.entrypoint == "add"
    assert request.cases[0].expected == SECRET_EXPECTED
    (answer,) = world.answers(task_id)
    assert answer.test_report["outcomes"][0]["passed"] is True
    assert {report["tool"] for report in answer.analysis} == {"ruff", "radon", "annotations"}
    assert answer.behavior == {"paste_count": 2, "focus_lost_count": 1, "time_spent_sec": 120}
    assert answer.ai_signal is None


def test_stdio_answer_is_run_as_a_program_with_template_check(world):
    stdio = {
        "io": "stdio",
        "entrypoint": None,
        "check": {"mode": "float"},
        "cases": [{"stdin": "1 2\n", "expected": "3\n"}],
    }
    _, (task_id,) = world.make_session([{"grade": "middle", "kind": "code", "hidden": stdio}])
    program = "import sys\nprint(sum(map(int, sys.stdin.read().split())))\n"
    assert _answer(world, task_id, content=program).status_code == 200
    request = world.state.sandbox.requests[0]
    assert request.io == "stdio" and request.entrypoint is None
    assert request.check.mode == "float"
    assert request.cases[0].stdin == "1 2\n"


def test_resubmission_replaces_answer(world):
    _, (task_id,) = world.make_session([{"grade": "middle", "kind": "code"}])
    _answer(world, task_id)
    second = "def add(a, b):\n    return b + a\n"
    assert _answer(world, task_id, content=second).status_code == 200
    (answer,) = world.answers(task_id)
    assert answer.content == second


def test_unknown_task_is_404(world):
    assert _answer(world, uuid.uuid4()).status_code == 404


def test_language_mismatch_is_422(world):
    _, (task_id,) = world.make_session([{"grade": "middle", "kind": "code"}])
    assert _answer(world, task_id, language="c++").status_code == 422


def test_no_sandbox_is_503(world):
    _, (task_id,) = world.make_session([{"grade": "middle", "kind": "code"}])
    world.state.sandbox = None
    assert _answer(world, task_id).status_code == 503


def test_unreachable_piston_is_503(world):
    # PistonRunner переводит сетевую ошибку в SandboxError, а не отдаёт httpx.HTTPError.
    class DownSandbox(FakeSandbox):
        async def run_tests(self, request):
            raise SandboxError("Piston недоступен: ConnectError")

    _, (task_id,) = world.make_session([{"grade": "middle", "kind": "code"}])
    world.state.sandbox = DownSandbox(_report([True]))
    assert _answer(world, task_id).status_code == 503


def test_score_session_end_to_end(world):
    session_id, task_ids = world.make_session(
        [
            {"grade": "junior", "kind": "code"},
            {"grade": "middle", "kind": "code"},
            {"grade": "middle", "kind": "essay"},
        ]
    )
    for task_id in task_ids[:2]:
        assert _answer(world, task_id).status_code == 200
    essay = "Ответ: использую оператор + для сложения"
    assert _answer(world, task_ids[2], content=essay, language=None).status_code == 200

    response = world.client.post(f"/sessions/{session_id}/score", headers=AUTH)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["outcome"] == "passed"
    assert result["assigned_grade"] == "middle"
    assert [task["solved"] for task in result["tasks"]] == [True, True, True]
    assert result["tasks"][0]["tests"] == {
        "passed": 2,
        "total": 2,
        "compile_error": False,
        "failed_required": [],
    }
    assert result["tasks"][2]["judge"]["yes"] == 1

    # Наружу не уходят ни скрытые тесты, ни рубрика, ни эталонный отчёт.
    for secret in (str(SECRET_EXPECTED), "uses_plus_operator", "оператором +", "reference"):
        assert secret not in response.text

    (answer,) = world.answers(task_ids[0])
    assert answer.score == pytest.approx(result["tasks"][0]["score"])
    assert answer.judge["verdicts"][0]["item_id"] == "uses_plus_operator"

    stored = world.client.get(f"/sessions/{session_id}/result", headers=AUTH)
    assert stored.status_code == 200
    assert stored.json() == result

    summary = world.client.get(f"/candidates/{world.candidate}/summary", headers=AUTH)
    assert summary.status_code == 200
    body = summary.json()
    assert body["data"]["sessions"][0]["outcome"] == "passed"
    # Модель ответила текстом без чисел, поэтому в summary — текст по шаблону.
    assert "молодец" not in body["text"]
    assert "Тест 1 из 1" in body["text"]


def _choose(world: World, task_id, choice: str, **body):
    payload = {"choice": choice} | body
    return world.client.post(f"/tasks/{task_id}/answer", json=payload, headers=AUTH)


def test_quiz_answer_is_option_id_and_wrong_shapes_are_422(world):
    _, (code, quiz) = world.make_session(
        [{"grade": "middle", "kind": "code"}, {"grade": "middle", "kind": "quiz"}]
    )
    assert _choose(world, quiz, "b").status_code == 200
    (answer,) = world.answers(quiz)
    assert answer.content == "b"
    # Неизвестный вариант, текст вместо варианта и вариант для задания с кодом — 422.
    unknown = _choose(world, quiz, "z")
    assert unknown.status_code == 422 and "z" in unknown.json()["detail"]
    as_text = world.client.post(f"/tasks/{quiz}/answer", json={"content": "c"}, headers=AUTH)
    assert as_text.status_code == 422
    both = _choose(world, quiz, "c", content="c")
    assert both.status_code == 422
    assert _choose(world, code, "a").status_code == 422
    assert world.client.post(f"/tasks/{code}/answer", json={}, headers=AUTH).status_code == 422


def test_quiz_block_is_one_component_and_stays_out_of_levels(world):
    session_id, task_ids = world.make_session(
        [
            {"grade": "middle", "kind": "code"},
            {"grade": "senior", "kind": "quiz"},
            {"grade": "senior", "kind": "quiz"},
            {"grade": "middle", "kind": "quiz"},
        ]
    )
    code, *quizzes = task_ids
    assert _answer(world, code).status_code == 200
    for task_id, choice in zip(quizzes, (QUIZ_KEY, "a", QUIZ_KEY), strict=True):
        assert _choose(world, task_id, choice).status_code == 200

    response = world.client.post(f"/sessions/{session_id}/score", headers=AUTH)
    assert response.status_code == 200, response.text
    result = response.json()
    assert [task["score"] for task in result["tasks"][1:]] == [1.0, 0.0, 1.0]
    # Уровень senior есть только у задачек, поэтому в оценке по уровням его нет.
    assert [level["grade"] for level in result["levels"]] == ["middle"]
    assert result["outcome"] == "passed"
    block = result["score"]
    assert block["quiz"] == {"correct": 2, "total": 3, "share": pytest.approx(2 / 3)}
    weight = world.state.settings.scoring.weight_quiz
    expected = (1 - weight) * result["tasks"][0]["score"] + weight * 2 / 3
    assert block["score"] == pytest.approx(expected)
    # Ключ ответа наружу не уходит.
    assert "answer_key" not in response.text and "quiz_variant" not in response.text

    summary = world.client.get(f"/candidates/{world.candidate}/summary", headers=AUTH).json()
    session = summary["data"]["sessions"][0]
    assert session["quiz"] == {"correct": 2, "total": 3}
    assert "задачек верно 2 из 3" in summary["text"]


def test_session_without_claimed_level_tasks_is_insufficient(world):
    session_id, task_ids = world.make_session(
        [{"grade": "junior", "kind": "code"}, {"grade": "senior", "kind": "code"}]
    )
    for task_id in task_ids:
        _answer(world, task_id)
    result = world.client.post(f"/sessions/{session_id}/score", headers=AUTH).json()
    assert result["outcome"] == "insufficient"
    assert result["assigned_grade"] is None
    recommendation = result["recommendation"]
    assert (recommendation["action"], recommendation["grade"]) == ("take_test", "middle")
    assert "middle" in recommendation["text"]


def _grouped_task(required: bool | None) -> dict:
    stress = {"weight": 1} | ({"required": required} if required is not None else {})
    cases = [
        {"input": [1, 2], "expected": 3, "group": "manual"},
        {"input": [2, 2], "expected": 4, "group": "manual"},
        {"input": [10**6, 1], "expected": 10**6 + 1, "group": "stress"},
    ]
    hidden = {"cases": cases, "case_groups": {"manual": {"weight": 10}, "stress": stress}}
    return {"grade": "middle", "kind": "code", "hidden": hidden}


@pytest.mark.parametrize(("required", "solved"), [(None, True), (False, True), (True, False)])
def test_failed_required_group_unsolves_task_with_high_score(world, required, solved):
    session_id, (task_id,) = world.make_session([_grouped_task(required)])
    world.state.sandbox = FakeSandbox(_report([True, True, False]))
    _answer(world, task_id)
    result = world.client.post(f"/sessions/{session_id}/score", headers=AUTH).json()
    [task] = result["tasks"]
    # Стресс-кейс весит 1 из 11: балл выше порога и с гейтом, и без него.
    assert task["score"] >= result["thresholds"]["solved"]
    assert task["solved"] is solved
    assert task["tests"]["failed_required"] == ([] if solved else ["stress"])
    assert result["outcome"] == ("passed" if solved else "failed")


def test_zero_pass_rate_scores_zero_without_judge(world):
    session_id, (task_id,) = world.make_session([{"grade": "middle", "kind": "code"}])
    world.state.sandbox = FakeSandbox(_report([False, False]))
    _answer(world, task_id)
    result = world.client.post(f"/sessions/{session_id}/score", headers=AUTH).json()
    assert result["tasks"][0]["score"] == 0.0
    assert result["outcome"] == "failed"
    assert result["recommendation"]["grade"] == "junior"
    assert world.state.responder.judge_calls == 0


def test_unanswered_task_scores_zero(world):
    session_id, _ = world.make_session([{"grade": "middle", "kind": "code"}])
    result = world.client.post(f"/sessions/{session_id}/score", headers=AUTH).json()
    assert result["tasks"][0]["answered"] is False
    assert result["tasks"][0]["score"] == 0.0


def test_answer_after_scoring_is_409(world):
    session_id, (task_id,) = world.make_session([{"grade": "middle", "kind": "code"}])
    _answer(world, task_id)
    world.client.post(f"/sessions/{session_id}/score", headers=AUTH)
    assert _answer(world, task_id).status_code == 409


def test_result_before_scoring_is_409_and_unknown_is_404(world):
    session_id, _ = world.make_session([{"grade": "middle", "kind": "code"}])
    assert world.client.get(f"/sessions/{session_id}/result", headers=AUTH).status_code == 409
    missing = world.client.get(f"/sessions/{uuid.uuid4()}/result", headers=AUTH)
    assert missing.status_code == 404
    assert world.client.post(f"/sessions/{uuid.uuid4()}/score", headers=AUTH).status_code == 404


@pytest.mark.parametrize(
    ("status", "fragment"),
    [(SessionStatus.GENERATING, "генерир"), (SessionStatus.FAILED, "не удалась")],
)
def test_session_not_ready_cannot_be_scored(world, status, fragment):
    session_id, (task_id,) = world.make_session(
        [{"grade": "middle", "kind": "code"}], status=status
    )
    _answer(world, task_id)
    response = world.client.post(f"/sessions/{session_id}/score", headers=AUTH)
    assert response.status_code == 409
    assert fragment in response.json()["detail"]
    assert world.client.get(f"/sessions/{session_id}/result", headers=AUTH).status_code == 409


def test_summary_of_unknown_candidate_is_404(world):
    response = world.client.get(f"/candidates/nobody-{uuid.uuid4()}/summary", headers=AUTH)
    assert response.status_code == 404


def test_judge_failure_is_502_and_session_stays_unscored(world):
    session_id, (task_id,) = world.make_session([{"grade": "middle", "kind": "code"}])
    _answer(world, task_id)
    world.state.llm = FakeLLM(lambda messages, schema: "не JSON")
    assert world.client.post(f"/sessions/{session_id}/score", headers=AUTH).status_code == 502
    assert world.client.get(f"/sessions/{session_id}/result", headers=AUTH).status_code == 409


# Значение, которое не может случайно совпасть с баллом или долей в результате.
AI_PROBABILITY = 0.4242424


class FixedDetector:
    async def detect(self, code: str, language: str) -> AISignal:
        return AISignal(detector="fixed", probability=AI_PROBABILITY, note="маркер-детектора")


def test_ai_detector_is_off_by_default_even_if_configured(world):
    _, (task_id,) = world.make_session([{"grade": "middle", "kind": "code"}])
    world.state.ai_detector = FixedDetector()
    _answer(world, task_id)
    assert world.answers(task_id)[0].ai_signal is None


def test_ai_signal_is_stored_but_does_not_leak_or_change_score(world):
    session_id, (task_id,) = world.make_session([{"grade": "middle", "kind": "code"}])
    _answer(world, task_id)
    plain = world.client.post(f"/sessions/{session_id}/score", headers=AUTH).json()

    other_id, (other_task,) = world.make_session([{"grade": "middle", "kind": "code"}])
    world.state.settings = world.state.settings.model_copy(update={"ai_detector_enabled": True})
    world.state.ai_detector = FixedDetector()
    _answer(world, other_task)
    assert world.answers(other_task)[0].ai_signal["probability"] == AI_PROBABILITY
    response = world.client.post(f"/sessions/{other_id}/score", headers=AUTH)
    detected = response.json()

    assert detected["tasks"][0]["score"] == pytest.approx(plain["tasks"][0]["score"])
    assert "маркер-детектора" not in response.text
    summary = world.summary()
    assert "маркер-детектора" not in summary.text + json.dumps(summary.data, ensure_ascii=False)
    assert str(AI_PROBABILITY) not in json.dumps(summary.data)
