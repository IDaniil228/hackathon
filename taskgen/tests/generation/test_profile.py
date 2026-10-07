"""Профиль кандидата: снимок в сессии, хэш и фиксация изменения профиля."""

from __future__ import annotations

import pytest

from taskgen.generation.profile import CandidateProfile, profile_hash
from tests.generation.world import ADMIN

pytestmark = pytest.mark.integration

PROFILE = {
    "resume": "Пишу на Python пять лет, люблю алгоритмы на графах.",
    "skills": ["python", "pytorch"],
    "extra": {"experience_years": 5, "roles": ["backend"], "links": ["https://example.org"]},
}


def check(world, session_id, **body):
    response = world.client.post(f"/sessions/{session_id}/profile", json=body, headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def test_hash_is_stable_and_order_independent_for_extra():
    first = CandidateProfile.model_validate(PROFILE)
    reordered = CandidateProfile.model_validate(
        PROFILE | {"extra": dict(reversed(list(PROFILE["extra"].items())))}
    )
    assert profile_hash(first) == profile_hash(reordered)
    changed = CandidateProfile.model_validate(PROFILE | {"resume": "Другое резюме"})
    assert profile_hash(changed) != profile_hash(first)


def test_snapshot_lives_until_last_task_then_only_hash_stays(world):
    world.add_templates(world.code("a"), world.code("b"))
    gated = world.gate(free=1)
    # Без навыка вне шаблона: смежная тема по seed сессии добавила бы первому заданию вызов
    # проверяльщика, и шлюз на один вызов задержал бы его.
    profile = PROFILE | {"skills": ["python"]}
    created = world.create(profile=profile)
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]
    expected = profile_hash(CandidateProfile.model_validate(profile))

    [session] = world.sessions()
    # Пока второе задание не готово, снимок нужен генерации.
    assert session.profile == CandidateProfile.model_validate(profile).model_dump(mode="json")
    assert session.profile_hash == expected

    world.open(gated)
    world.wait()
    [session] = world.sessions()
    assert session.status == "ready"
    assert session.profile is None
    assert session.profile_hash == expected
    view = world.view(session_id)
    assert view["profile_hash"] == expected
    assert view["profile_changed"] is False
    assert "resume" not in str(view)


def test_personal_tasks_see_profile(world):
    world.add_templates(world.code("a"), world.essay())
    session = world.create_ready(profile=PROFILE)
    # Проверяльщик ситуации эссе профиль не видит: ему он не нужен.
    writers = [
        call
        for call in world.state.llm.calls
        if "facts" not in (call["json_schema"] or {}).get("properties", {})
    ]
    prompts = ["\n".join(m.content for m in call["messages"]) for call in writers]
    assert len(prompts) == 2
    assert all(PROFILE["resume"] in prompt for prompt in prompts)
    # Навык профиля вне шаблонов пишется названием из справочника, а не кодом.
    assert all("PyTorch" in prompt for prompt in prompts)
    for task in world.tasks(session["id"]):
        assert task.personalization["profile"] is True


def test_snapshot_is_deleted_when_session_fails(world):
    world.add_templates(world.broken())
    response = world.create(profile=PROFILE)
    assert response.status_code == 500
    world.wait()
    [session] = world.sessions()
    assert session.status == "failed"
    assert session.profile is None
    assert session.profile_hash


def test_profile_check_marks_change_only(world):
    world.add_templates(world.code())
    session = world.create_ready(profile=PROFILE)
    same = check(world, session["id"], profile=PROFILE)
    assert same == {"profile_changed": False}
    by_hash = check(world, session["id"], profile_hash=session["profile_hash"])
    assert by_hash == {"profile_changed": False}

    changed = check(world, session["id"], profile=PROFILE | {"resume": "Новое резюме"})
    assert changed == {"profile_changed": True}
    # Факт фиксируется и не сбрасывается, а сама сессия и задания не меняются.
    assert check(world, session["id"], profile=PROFILE) == {"profile_changed": True}
    view = world.view(session["id"])
    assert view["profile_changed"] is True
    assert view["status"] == "ready"
    assert [t["statement"] for t in view["tasks"]] == [t["statement"] for t in session["tasks"]]


def test_session_without_profile_differs_from_any_profile(world):
    world.add_templates(world.code())
    session = world.create_ready()
    assert session["profile_hash"] is None
    assert check(world, session["id"], profile=PROFILE) == {"profile_changed": True}


@pytest.mark.parametrize(
    "body", [{}, {"profile": PROFILE, "profile_hash": "0" * 64}, {"profile_hash": "xyz"}]
)
def test_profile_check_needs_exactly_one_valid_field(world, body):
    world.add_templates(world.code())
    session = world.create_ready()
    response = world.client.post(f"/sessions/{session['id']}/profile", json=body, headers=ADMIN)
    assert response.status_code == 422


def test_profile_check_of_unknown_session_is_404(world):
    response = world.client.post(
        "/sessions/00000000-0000-0000-0000-000000000000/profile",
        json={"profile": PROFILE},
        headers=ADMIN,
    )
    assert response.status_code == 404
