"""API сессий на настоящей БД с подделками LLM и песочницы."""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from taskgen.app import create_app
from taskgen.config import GenerationSettings
from taskgen.contracts import HiddenPayload
from taskgen.db import Task
from tests.evaluation.fakes import FakeLLM
from tests.generation.fakes import LocalSandbox
from tests.generation.templates import ENTRYPOINT, REFERENCE
from tests.generation.world import ADMIN, ModelStub, World, fake_world

pytestmark = pytest.mark.integration


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
    assert response.headers["location"] == f"/sessions/{response.json()['id']}"
    world.wait()
    body = world.view(response.json()["id"], client_headers)
    assert body["status"] == "ready"
    assert body["failure"] is None
    assert body["claimed_grade"] == "junior"
    assert sorted(task["kind"] for task in body["tasks"]) == ["code", "essay"]
    assert [task["position"] for task in body["tasks"]] == [1, 2]
    assert [task["status"] for task in body["tasks"]] == ["ready", "ready"]

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
    assert public_code["public"]["signature"] == {
        "io": "function",
        "entrypoint": ENTRYPOINT,
        "runtime": "python",
    }
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


def test_quiz_session_end_to_end_keeps_answer_key_hidden(world, client_headers):
    world.add_templates(world.code(), world.quiz())
    created = world.create_ready(headers=client_headers, seed=5)
    assert created["status"] == "ready"
    quiz = next(task for task in created["tasks"] if task["kind"] == "quiz")
    options = quiz["public"]["options"]
    assert [option["id"] for option in options] == ["a", "b", "c", "d"]
    [row] = [row for row in world.tasks(created["id"]) if row.kind == "quiz"]
    key = row.hidden_payload["answer_key"]
    variant = row.hidden_payload["quiz_variant"]

    texts = [
        world.client.get(f"/sessions/{created['id']}", headers=client_headers).text,
        world.client.get(f"/tasks/{row.id}", headers=client_headers).text,
        world.client.get(f"/sessions/{created['id']}/selection", headers=client_headers).text,
    ]
    for text in texts:
        for secret in ("answer_key", "quiz_variant", f'"{variant}"', "correct"):
            assert secret not in text

    answered = world.client.post(
        f"/tasks/{row.id}/answer", json={"choice": key}, headers=client_headers
    )
    assert answered.status_code == 200, answered.text
    scored = world.client.post(f"/sessions/{created['id']}/score", headers=client_headers)
    assert scored.status_code == 200, scored.text
    result = scored.json()
    assert result["score"]["quiz"] == {"correct": 1, "total": 1, "share": 1.0}
    assert all(level["total"] == 1 for level in result["levels"])


def test_background_quiz_needs_two_model_calls(world):
    # Первое задание — код (один вызов), задачка в фоне ждёт свои два вызова: кейс и
    # проверку фактов. free=1 держит её, пока тест не откроет модель.
    world.add_templates(world.code(), world.quiz())
    gated = world.gate(free=1)
    created = world.create(selection_override={"code_tasks": 1, "quiz_tasks": 1}).json()
    assert [task["kind"] for task in created["tasks"]] == ["code", "quiz"]
    assert [task["status"] for task in created["tasks"]] == ["ready", "pending"]
    world.open(gated)
    world.wait()
    assert [task["status"] for task in world.view(created["id"])["tasks"]] == ["ready", "ready"]


def test_first_task_is_ready_at_once_and_rest_arrive_in_background(world):
    # Только задания с кодом: каждому нужен ровно один вызов модели, и free=1 пропускает
    # ровно первое задание. Эссе нужно два вызова (ситуация и проверка фактов): выпади оно
    # первым по случайному отбору, его второй вызов ждал бы gated.open() вечно.
    world.add_templates(world.code("a"), world.code("b"), world.code("c"))
    gated = world.gate(free=1)
    response = world.create()
    assert response.status_code == 201, response.text
    created = response.json()
    # Ответ пришёл, когда готово только первое задание: остальные ждут модель.
    assert created["status"] == "generating"
    assert [task["status"] for task in created["tasks"]] == ["ready", "pending", "pending"]
    first, *rest = created["tasks"]
    assert first["statement"] and first["public"]
    assert all(task["statement"] is None and task["public"] is None for task in rest)

    world.open(gated)
    world.wait()
    view = world.view(created["id"])
    assert view["status"] == "ready"
    assert [task["status"] for task in view["tasks"]] == ["ready"] * 3
    assert all(task["statement"] for task in view["tasks"])


def test_pending_task_takes_no_answer_and_session_is_not_scored(world):
    world.add_templates(world.code("a"), world.code("b"))
    world.gate(free=1)
    created = world.create().json()
    pending = next(task for task in created["tasks"] if task["status"] == "pending")

    task = world.client.get(f"/tasks/{pending['id']}", headers=ADMIN).json()
    assert task["status"] == "pending" and task["statement"] is None
    answer = {"content": "def top_sum(nums, k):\n    return 0\n", "language": "python"}
    answered = world.client.post(f"/tasks/{pending['id']}/answer", json=answer, headers=ADMIN)
    assert answered.status_code == 409
    assert "не готово" in answered.json()["detail"]
    # Первое, готовое задание кандидат решает, пока остальные пишутся.
    ready = world.client.post(
        f"/tasks/{created['tasks'][0]['id']}/answer", json=answer, headers=ADMIN
    )
    assert ready.status_code == 200, ready.text

    scored = world.client.post(f"/sessions/{created['id']}/score", headers=ADMIN)
    assert scored.status_code == 409
    assert "генерир" in scored.json()["detail"]


def test_selection_is_stored_and_explained(world, client_headers):
    world.add_templates(world.code("a"), world.code("b"), world.essay())
    created = world.create_ready(headers=client_headers, seed=7)
    response = world.client.get(f"/sessions/{created['id']}/selection", headers=client_headers)
    assert response.status_code == 200
    explanation = response.json()
    assert explanation["seed"] == 7
    assert explanation["candidates"] == 3
    assert explanation["parameters"]["temperature"] == world.state.settings.selection.temperature
    assert explanation["replacements"] == []
    names = [item["name"] for item in explanation["selected"]]
    assert sorted(names) == sorted(f"{world.prefix}-{s}" for s in ("a", "b", "essay"))
    # Позиции заданий идут в порядке выбора шаблонов.
    rows = world.tasks(created["id"])
    assert [str(row.template_id) for row in rows] == [
        item["template_id"] for item in explanation["selected"]
    ]
    [session] = world.sessions()
    assert session.selection["selected"] == explanation["selected"]


def test_quota_slots_are_explained_and_shortage_is_a_conflict(world, client_headers):
    world.add_templates(world.code("a"), world.code("b", grade="middle"), world.essay())
    quota = {"code_tasks": 2, "essay_tasks": 1, "quiz_tasks": 0, "min_claimed_per_kind": 1}
    created = world.create_ready(selection_override=quota)
    explanation = world.client.get(
        f"/sessions/{created['id']}/selection", headers=client_headers
    ).json()
    rows = [(item["kind"], item["slot"]) for item in explanation["selected"]]
    assert rows == [("code", "quota"), ("code", "explore"), ("essay", "quota")]
    assert explanation["selected"][0]["grade"] == "junior"

    # Эссе грейда middle в банке нет: тест на middle не собирается, а не берёт junior молча.
    response = world.create(claimed_grade="middle", selection_override=quota)
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert "essay" in detail and "middle" in detail and "min_claimed_per_kind" in detail


def test_same_seed_reproduces_session(world):
    world.add_templates(world.code("a"), world.code("b"), world.essay())
    first = world.create_ready(seed=123)
    second = world.create_ready(seed=123)
    rows_first, rows_second = world.tasks(first["id"]), world.tasks(second["id"])
    assert [(r.template_id, r.seed, r.params) for r in rows_first] == [
        (r.template_id, r.seed, r.params) for r in rows_second
    ]
    assert [r.hidden_payload["cases"] for r in rows_first] == [
        r.hidden_payload["cases"] for r in rows_second
    ]


def test_client_cannot_override_selection(world, client_headers):
    world.add_templates(world.code(), world.essay())
    override = {"selection_override": {"code_tasks": 1, "essay_tasks": 0}}
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
    world.wait()
    view = world.view(detail["session_id"])
    assert view["status"] == "failed"
    assert fragment in view["failure"]
    assert all(task["status"] == "failed" for task in view["tasks"])
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


def test_broken_reference_without_replacement_is_500_and_names_template(world):
    world.add_templates(world.broken())
    _failed(world, world.create(), 500, f"{world.prefix}-bad")


def test_failed_template_is_replaced_by_next_of_same_grade_and_kind(world):
    # Шаблон middle при равных вероятностях стоит в ранжировании раньше (по имени): замена
    # без проверки грейда взяла бы его. Эссе junior — единственное своего типа, у него
    # вероятность 1, и в ранжировании оно первое: замена без проверки типа взяла бы его.
    world.add_templates(
        world.broken(),
        world.code("good"),
        world.code("aaa", grade="middle"),
        world.essay("aaa-essay"),
    )
    bad, good = f"{world.prefix}-bad", f"{world.prefix}-good"
    # Температура 1 уравнивает вероятности: порядок замен решают только грейд и имя.
    override = {"selection_override": {"code_tasks": 1, "essay_tasks": 0, "temperature": 1.0}}
    # Какой шаблон выпадет первым, решает seed; ищется seed, при котором выпал сломанный.
    for seed in range(40):
        response = world.create(seed=seed, **override)
        assert response.status_code == 201, response.text
        session_id = response.json()["id"]
        explanation = world.client.get(f"/sessions/{session_id}/selection", headers=ADMIN).json()
        if explanation["selected"][0]["name"] == bad:
            break
    else:
        pytest.fail("ни один seed не выбрал сломанный шаблон")

    world.wait()
    view = world.view(session_id)
    assert view["status"] == "ready"
    [task] = world.tasks(session_id)
    [session] = [row for row in world.sessions() if str(row.id) == session_id]
    ranking = {entry["name"]: entry["template_id"] for entry in session.selection["ranking"]}
    # Замена — того же грейда и типа: шаблон middle и эссе не подходят, хотя они в отборе.
    assert str(task.template_id) == ranking[good]
    assert task.kind == "code"
    explanation = world.client.get(f"/sessions/{session_id}/selection", headers=ADMIN).json()
    [replacement] = explanation["replacements"]
    assert replacement["position"] == 1
    assert replacement["name"] == bad
    assert "эталон" in replacement["reason"]


def test_replacement_limit_stops_replacing(world):
    world.state.settings = world.state.settings.model_copy(
        update={
            "generation": GenerationSettings(autostart=False, pool_size=0, replacements_per_task=0)
        }
    )
    world.add_templates(world.broken(), world.code("good"))
    override = {"selection_override": {"code_tasks": 1, "temperature": 1.0}}
    statuses = {world.create(seed=seed, **override).status_code for seed in range(20)}
    # Где выпал сломанный шаблон, замены нет даже при свободном шаблоне того же грейда.
    assert statuses == {201, 500}


COMPOSITION_SESSIONS = 16


def test_replacements_keep_composition_and_claimed_grade_quota(world):
    # Гарантия состава на уровне сессии: сломанный шаблон кода заявленного грейда выпадает в
    # квотный слот примерно в половине сессий, и замена обязана оставить задание кодом junior.
    world.add_templates(
        world.broken(),
        world.code("good"),
        world.code("mid", grade="middle"),
        world.essay("e-junior"),
        world.essay("e-middle", grade="middle"),
    )
    override = {
        "code_tasks": 1,
        "essay_tasks": 1,
        "quiz_tasks": 0,
        "min_claimed_per_kind": 1,
        "temperature": 1.0,
    }
    replaced = 0
    for seed in range(COMPOSITION_SESSIONS):
        created = world.create_ready(seed=seed, selection_override=override)
        assert created["status"] == "ready", created
        rows = world.tasks(created["id"])
        assert [row.kind for row in rows] == ["code", "essay"]
        names = {t.id: name for name, t in world.templates().items()}
        assert [names[row.template_id] for row in rows][0] == "good"
        assert names[rows[1].template_id] == "e-junior"
        replaced += bool(rows[0].replacements)
    assert 0 < replaced < COMPOSITION_SESSIONS


def test_code_task_is_not_replaced_by_template_of_another_kind(world):
    # Свободный шаблон того же грейда есть, но это эссе: состав теста по типам не меняется,
    # и задание с кодом без замены своего типа не создаётся.
    world.add_templates(world.broken(), world.essay())
    response = world.create(selection_override={"code_tasks": 1, "essay_tasks": 0})
    assert response.status_code == 500, response.text
    world.wait()
    [session] = world.sessions()
    assert session.status == "failed"
    assert "типа code" in session.failure
    [task] = world.tasks(session.id)
    assert (task.kind, task.status, task.replacements) == ("code", "failed", [])


def test_failure_without_same_grade_replacement_fails_session(world):
    # Оба шаблона junior попадают в сессию, поэтому заменить сломанный нечем.
    world.add_templates(world.code("good"), world.broken())
    response = world.create(selection_override={"code_tasks": 2})
    # Сломанный шаблон мог выпасть первым (ответ 500) или вторым (сбой в фоне).
    assert response.status_code in (201, 500), response.text
    world.wait()
    [session] = world.sessions()
    assert session.status == "failed"
    assert f"{world.prefix}-bad" in session.failure
    assert "замен" in session.failure
    statuses = {task.status for task in world.tasks(session.id)}
    assert "pending" not in statuses and "failed" in statuses


def test_get_unknown_session_and_task_are_404(world):
    assert world.client.get(f"/sessions/{uuid.uuid4()}", headers=ADMIN).status_code == 404
    assert world.client.get(f"/sessions/{uuid.uuid4()}/selection", headers=ADMIN).status_code == 404
    assert world.client.get(f"/tasks/{uuid.uuid4()}", headers=ADMIN).status_code == 404


def test_unfinished_session_is_resumed_after_restart(settings):
    # Первый запуск: ответ с первым заданием, остальные стоят в очереди к модели, когда
    # сервис останавливается.
    with TestClient(create_app(settings)) as client:
        world = fake_world(client)
        # Только код: free=1 должен пропустить ровно первое задание, а эссе нужно два вызова.
        world.add_templates(world.code("a"), world.code("b"), world.code("c"))
        world.gate(free=1)
        created = world.create().json()
        assert created["status"] == "generating"
    area, candidate, prefix = world.area, world.candidate, world.prefix

    # Пул выключен: иначе он пополнялся бы по всем шаблонам общей тестовой БД.
    restarted = settings.model_copy(
        update={"generation": GenerationSettings(autostart=True, pool_size=0)}
    )
    with TestClient(create_app(restarted, llm=FakeLLM(ModelStub()), sandbox=LocalSandbox())) as c:
        again = World.__new__(World)
        again.client, again.area, again.candidate, again.prefix = c, area, candidate, prefix
        try:
            again.wait()
            view = again.view(created["id"])
            assert view["status"] == "ready"
            assert [task["status"] for task in view["tasks"]] == ["ready"] * 3
        finally:
            again.cleanup()


def test_end_to_end_generation_answer_and_scoring(world):
    world.add_templates(world.code(), world.essay())
    session = world.create_ready(seed=5)
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
    assert code["tests"] == {
        "passed": total,
        "total": total,
        "compile_error": False,
        "failed_required": [],
    }
    # Скорость оценена: оценка нашла отчёт эталона, который записала генерация.
    assert code["components"]["performance"] is not None
    assert all(task["solved"] for task in result["tasks"])
    assert result["assigned_grade"] == "junior"

    view = world.view(session["id"])
    assert view["status"] == "scored"
    assert view["assigned_grade"] == "junior"
