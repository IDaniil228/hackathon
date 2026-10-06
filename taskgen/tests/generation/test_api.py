"""API сессий на настоящей БД с подделками LLM и песочницы.

Каждый тест работает в своей области: отбор не видит шаблоны других тестов.
"""

from __future__ import annotations

import json
import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from taskgen.app import create_app
from taskgen.bank.importer import TemplateDocument, import_documents
from taskgen.contracts import HiddenPayload
from taskgen.db import Area, AreaSkill, CandidateSummary, Task, Template, TestSession
from tests.conftest import ADMIN_KEY
from tests.evaluation.fakes import FakeLLM, http_error
from tests.generation.fakes import LocalSandbox
from tests.generation.templates import ENTRYPOINT, REFERENCE, code_template_md, essay_template_md

pytestmark = pytest.mark.integration

ADMIN = {"Authorization": f"Bearer {ADMIN_KEY}"}
ENTRYPOINT_IN_PROMPT = re.compile(r"функцию `(\w+)`")
ANSWER_IN_JUDGE = re.compile(r"<<<ОТВЕТ-\w+>>>\n(.*?)\n<<<КОНЕЦ", re.DOTALL)


class ModelStub:
    """Отвечает за генератора, судью и summary; что именно спрошено, видно по схеме."""

    def __init__(self) -> None:
        self.down = False

    def __call__(self, messages, schema):
        if self.down:
            raise http_error(503)
        properties = (schema or {}).get("properties", {})
        if "sentences" in properties:
            return json.dumps({"sentences": ["Кандидат решил задания."]}, ensure_ascii=False)
        if "items" in properties:
            answer = ANSWER_IN_JUDGE.search(messages[-1].content).group(1)
            quote = next(line for line in answer.splitlines() if line.strip())
            ids = properties["items"]["items"]["properties"]["item_id"]["enum"]
            items = [{"item_id": i, "verdict": "yes", "quotes": [quote]} for i in ids]
            return json.dumps({"items": items}, ensure_ascii=False)
        prompt = messages[1].content
        match = ENTRYPOINT_IN_PROMPT.search(prompt)
        statement = f"Напишите функцию {match.group(1)}." if match else "Расскажите про GIL."
        return json.dumps({"title": "Задание", "statement": statement}, ensure_ascii=False)


class World:
    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.area = f"g{uuid.uuid4().hex[:12]}"
        self.candidate = f"cand-{uuid.uuid4().hex[:8]}"
        self.prefix = f"g{uuid.uuid4().hex[:10]}"
        self._run(self._create_area)

    @property
    def state(self):
        return self.client.app.state

    def _run(self, fn, *args):
        return self.client.portal.call(fn, *args)

    async def _create_area(self) -> None:
        async with self.state.sessionmaker() as db, db.begin():
            db.add(Area(code=self.area, title="Тестовая область"))
            await db.flush()
            db.add(AreaSkill(area_code=self.area, skill_code="python"))

    def add_templates(self, *bodies: str) -> None:
        async def load():
            async with self.state.sessionmaker() as db, db.begin():
                docs = [TemplateDocument(f"t{i}.md", body) for i, body in enumerate(bodies)]
                await import_documents(db, docs)

        self._run(load)

    def code(self, suffix: str = "code", **frontmatter) -> str:
        frontmatter.setdefault("skills", ["python"])
        return code_template_md(name=f"{self.prefix}-{suffix}", area=self.area, **frontmatter)

    def essay(self, suffix: str = "essay", **frontmatter) -> str:
        frontmatter.setdefault("skills", ["python"])
        return essay_template_md(name=f"{self.prefix}-{suffix}", area=self.area, **frontmatter)

    def create(self, headers=ADMIN, **body):
        payload = {
            "candidate_ref": self.candidate,
            "area": self.area,
            "claimed_grade": "junior",
            "skills": ["python"],
        } | body
        return self.client.post("/sessions", json=payload, headers=headers)

    def tasks(self, session_id) -> list[Task]:
        async def load():
            async with self.state.sessionmaker() as db:
                query = select(Task).where(Task.session_id == session_id).order_by(Task.position)
                return list(await db.scalars(query))

        return self._run(load)

    def sessions(self) -> list[TestSession]:
        async def load():
            async with self.state.sessionmaker() as db:
                query = select(TestSession).where(TestSession.candidate_ref == self.candidate)
                return list(await db.scalars(query))

        return self._run(load)

    def cleanup(self) -> None:
        async def drop():
            async with self.state.sessionmaker() as db, db.begin():
                await db.execute(
                    delete(TestSession).where(TestSession.candidate_ref == self.candidate)
                )
                await db.execute(delete(Template).where(Template.area_code == self.area))
                await db.execute(delete(AreaSkill).where(AreaSkill.area_code == self.area))
                await db.execute(delete(Area).where(Area.code == self.area))
                await db.execute(
                    delete(CandidateSummary).where(CandidateSummary.candidate_ref == self.candidate)
                )

        self._run(drop)


@pytest.fixture
def world(settings):
    with TestClient(create_app(settings)) as client:
        client.portal.call(client.app.state.llm.aclose)
        client.app.state.model_stub = ModelStub()
        client.app.state.llm = FakeLLM(client.app.state.model_stub)
        client.app.state.sandbox = LocalSandbox()
        w = World(client)
        try:
            yield w
        finally:
            w.cleanup()


@pytest.fixture
def client_headers(world) -> dict[str, str]:
    issued = world.client.post("/keys", json={"name": "backend", "role": "client"}, headers=ADMIN)
    assert issued.status_code == 201
    return {"Authorization": f"Bearer {issued.json()['key']}"}


def _secrets(task: Task) -> list[str]:
    """Значения скрытых кейсов, которых нет среди публичных примеров, в виде из ответа API."""
    hidden = HiddenPayload.model_validate(task.hidden_payload)
    shown = len(task.public_payload["examples"])
    assert len(hidden.cases) > shown
    # Ответ FastAPI — компактный JSON; с пробелами после запятых значение в нём не найдётся.
    return [json.dumps(case.input[0], separators=(",", ":")) for case in hidden.cases[shown:]]


def test_requires_key(world):
    assert world.client.post("/sessions", json={}).status_code == 401
    assert world.client.get(f"/sessions/{uuid.uuid4()}").status_code == 401


def test_session_with_code_and_essay(world, client_headers):
    world.add_templates(world.code(), world.essay())
    response = world.create(headers=client_headers, seed=42)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["failure"] is None
    assert body["claimed_grade"] == "junior"
    assert sorted(task["kind"] for task in body["tasks"]) == ["code", "essay"]
    assert [task["position"] for task in body["tasks"]] == [1, 2]

    rows = world.tasks(body["id"])
    code = next(row for row in rows if row.kind == "code")
    essay = next(row for row in rows if row.kind == "essay")
    assert set(code.hidden_payload) == set(HiddenPayload.model_fields)
    assert code.validated is True
    assert code.hidden_payload["reference_report"]["outcomes"]
    assert essay.hidden_payload["cases"] == []
    assert essay.hidden_payload["rubric"]

    public_code = next(task for task in body["tasks"] if task["kind"] == "code")
    assert public_code["statement"] == f"Напишите функцию {ENTRYPOINT}."
    assert public_code["public"]["signature"] == {"entrypoint": ENTRYPOINT, "runtime": "python"}
    assert len(public_code["public"]["examples"]) == 2

    # Ни скрытые кейсы, ни рубрика, ни эталон не уходят наружу ни одним эндпоинтом.
    texts = [
        response.text,
        world.client.get(f"/sessions/{body['id']}", headers=client_headers).text,
        world.client.get(f"/tasks/{code.id}", headers=client_headers).text,
        world.client.get(f"/sessions/{body['id']}/selection", headers=client_headers).text,
    ]
    secrets = [*_secrets(code), "reference_report", "hidden", "rubric", "sorted(nums"]
    # Контроль формы: публичный пример в том же виде в ответе находится.
    shown = json.dumps(code.public_payload["examples"][0]["input"][0], separators=(",", ":"))
    assert shown in texts[2]
    for text in texts:
        for secret in secrets:
            assert secret not in text


def test_selection_is_stored_and_explained(world, client_headers):
    world.add_templates(world.code("a"), world.code("b"), world.essay())
    created = world.create(headers=client_headers, seed=7).json()
    response = world.client.get(f"/sessions/{created['id']}/selection", headers=client_headers)
    assert response.status_code == 200
    explanation = response.json()
    assert explanation["seed"] == 7
    assert explanation["candidates"] == 3
    assert explanation["parameters"]["temperature"] == world.state.settings.selection.temperature
    names = [item["name"] for item in explanation["selected"]]
    assert sorted(names) == sorted(f"{world.prefix}-{s}" for s in ("a", "b", "essay"))
    # Позиции заданий идут в порядке выбора шаблонов.
    rows = world.tasks(created["id"])
    assert [str(row.template_id) for row in rows] == [
        item["template_id"] for item in explanation["selected"]
    ]
    [session] = world.sessions()
    assert session.selection["selected"] == explanation["selected"]


def test_same_seed_reproduces_session(world):
    world.add_templates(world.code("a"), world.code("b"), world.essay())
    first = world.create(seed=123).json()
    second = world.create(seed=123).json()
    rows_first, rows_second = world.tasks(first["id"]), world.tasks(second["id"])
    assert [(r.template_id, r.seed, r.params) for r in rows_first] == [
        (r.template_id, r.seed, r.params) for r in rows_second
    ]
    assert [r.hidden_payload["cases"] for r in rows_first] == [
        r.hidden_payload["cases"] for r in rows_second
    ]


def test_client_cannot_override_selection(world, client_headers):
    world.add_templates(world.code(), world.essay())
    override = {"selection_override": {"tasks_per_test": 1}}
    denied = world.create(headers=client_headers, **override)
    assert denied.status_code == 403
    assert world.sessions() == []
    allowed = world.create(**override)
    assert allowed.status_code == 201, allowed.text
    assert len(allowed.json()["tasks"]) == 1


def test_client_cannot_pass_selection_parameters_directly(world, client_headers):
    world.add_templates(world.code())
    assert world.create(headers=client_headers, temperature=1.0).status_code == 422


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ({"area": "nope"}, "област"),
        ({"claimed_grade": "guru"}, "грейд"),
        ({"skills": ["nope"]}, "nope"),
        ({"skills": ["pytorch"]}, "pytorch"),
        ({"selection_override": {"temperature": 5}}, "temperature"),
    ],
)
def test_invalid_request_is_422(world, body, fragment):
    world.add_templates(world.code())
    response = world.create(**body)
    assert response.status_code == 422
    assert fragment in json.dumps(response.json(), ensure_ascii=False)
    assert world.sessions() == []


def test_empty_area_is_409(world):
    response = world.create()
    assert response.status_code == 409
    assert world.sessions() == []


def _failed(world, response, status: int, fragment: str) -> None:
    assert response.status_code == status, response.text
    detail = response.json()["detail"]
    assert fragment in detail["message"]
    view = world.client.get(f"/sessions/{detail['session_id']}", headers=ADMIN).json()
    assert view["status"] == "failed"
    assert fragment in view["failure"]
    assert view["tasks"] == []
    # Причина хранится в своей колонке, а объяснение отбора остаётся объяснением.
    [session] = world.sessions()
    assert fragment in session.failure
    assert "failure" not in session.selection


def test_no_sandbox_is_503_and_session_failed(world):
    world.add_templates(world.code())
    world.state.sandbox = None
    _failed(world, world.create(), 503, "Песочница")


def test_llm_down_is_502_and_session_failed(world):
    world.add_templates(world.code(), world.essay())
    world.state.model_stub.down = True
    _failed(world, world.create(), 502, "Модель")


def test_broken_reference_is_500_and_names_template(world):
    reference = "```python\ndef top_sum(nums, k):\n    return 0\n```"
    world.add_templates(world.code(sections={"Эталон": reference}))
    _failed(world, world.create(), 500, f"{world.prefix}-code")


def test_get_unknown_session_and_task_are_404(world):
    assert world.client.get(f"/sessions/{uuid.uuid4()}", headers=ADMIN).status_code == 404
    assert world.client.get(f"/sessions/{uuid.uuid4()}/selection", headers=ADMIN).status_code == 404
    assert world.client.get(f"/tasks/{uuid.uuid4()}", headers=ADMIN).status_code == 404


def test_end_to_end_generation_answer_and_scoring(world):
    world.add_templates(world.code(), world.essay())
    session = world.create(seed=5).json()
    assert session["status"] == "ready"
    for task in session["tasks"]:
        if task["kind"] == "code":
            content = REFERENCE.removeprefix("```python\n").removesuffix("\n```") + "\n"
            body = {"content": content, "language": "python"}
        else:
            body = {"content": "GIL не даёт потокам Python исполнять байткод параллельно."}
        answered = world.client.post(f"/tasks/{task['id']}/answer", json=body, headers=ADMIN)
        assert answered.status_code == 200, answered.text

    scored = world.client.post(f"/sessions/{session['id']}/score", headers=ADMIN)
    assert scored.status_code == 200, scored.text
    result = scored.json()
    code = next(task for task in result["tasks"] if task["kind"] == "code")
    rows = world.tasks(session["id"])
    total = len(next(r for r in rows if r.kind == "code").hidden_payload["cases"])
    assert code["tests"] == {"passed": total, "total": total, "compile_error": False}
    # Скорость оценена: оценка нашла отчёт эталона, который записала генерация.
    assert code["components"]["performance"] is not None
    assert all(task["solved"] for task in result["tasks"])
    assert result["assigned_grade"] == "junior"

    view = world.client.get(f"/sessions/{session['id']}", headers=ADMIN).json()
    assert view["status"] == "scored"
    assert view["assigned_grade"] == "junior"
