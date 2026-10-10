import asyncio
from uuid import UUID, uuid4

import httpx
from test_api import account
from test_services import prepared_survey

from app.db.models.assess import TestSession as Attempt
from app.grading import apply_result
from app.operations import process_operation


async def prepare_remote(client):
    headers, user_id = await account(client)
    survey_id = await prepared_survey(client, user_id)
    remote_id = str(uuid4())
    entered, resume = asyncio.Event(), asyncio.Event()
    remote_session = {
        "id": remote_id,
        "candidate_ref": user_id,
        "area": "backend",
        "claimed_grade": "middle",
        "status": "ready",
        "tasks_issued": True,
        "tasks": [
            {
                "id": str(uuid4()),
                "status": "ready",
                "kind": "code",
                "statement": "Задание",
                "public": {},
            }
        ],
    }

    async def remote(request):
        if request.method == "POST" and request.url.path == "/sessions":
            return httpx.Response(201, json=remote_session)
        entered.set()
        await resume.wait()
        return httpx.Response(200, json=remote_session)

    client.app.state.settings.taskgen_client_key = "test-client"
    original = client.app.state.http
    client.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    response = await client.post(
        "/tests",
        headers={**headers, "Idempotency-Key": "race-create-session"},
        json={"survey_session_id": survey_id},
    )
    assert response.status_code == 202, response.text
    value = response.json()
    await process_operation(client.app, UUID(value["id"]))
    return headers, value["resource_id"], remote_id, entered, resume, original


async def test_poll_cannot_restore_cancelled_attempt(client):
    headers, local_id, _, entered, resume, original = await prepare_remote(client)
    try:
        polling = asyncio.create_task(client.get(f"/tests/{local_id}", headers=headers))
        await asyncio.wait_for(entered.wait(), 5)
        cancelled = await client.post(f"/tests/{local_id}/cancel", headers=headers)
        assert cancelled.status_code == 200, cancelled.text
        resume.set()
        response = await polling
        assert response.json()["status"] == "cancelled"
        assert response.json()["tasks"] == []
    finally:
        resume.set()
        await client.app.state.http.aclose()
        client.app.state.http = original


async def test_finish_cannot_restore_submitted_after_result_was_applied(client):
    headers, local_id, remote_id, entered, resume, original = await prepare_remote(client)
    try:
        finishing = asyncio.create_task(client.post(f"/tests/{local_id}/finish", headers=headers))
        await asyncio.wait_for(entered.wait(), 5)
        async with client.app.state.sessionmaker() as session:
            await apply_result(
                session,
                UUID(local_id),
                {
                    "session_id": remote_id,
                    "claimed_grade": "middle",
                    "outcome": "passed",
                    "levels": [{"grade": "middle", "confirmed": True}],
                    "score": {"score": 0.9},
                },
            )
            await session.commit()
        resume.set()
        response = await finishing
        assert response.json().get("status") == "done", response.text
        async with client.app.state.sessionmaker() as session:
            assert str((await session.get(Attempt, UUID(local_id))).status) == "scored"
    finally:
        resume.set()
        await client.app.state.http.aclose()
        client.app.state.http = original
