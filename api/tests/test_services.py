from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from test_api import account

from app.db import enums
from app.db.models.assess import GradeAssignment, SurveySession, SurveyTemplate
from app.db.models.assess import TestSession as Attempt
from app.db.models.ref import Grade, Industry, Specialization
from app.operations import process_operation


async def prepared_survey(client, candidate_id):
    async with client.app.state.sessionmaker() as session:
        industry = await session.scalar(select(Industry).where(Industry.code == "it"))
        spec = await session.scalar(select(Specialization).where(Specialization.code == "backend"))
        grade = await session.scalar(
            select(Grade).where(Grade.industry_id == industry.id, Grade.code == "middle")
        )
        template = SurveyTemplate(
            industry_id=industry.id, code="test_" + uuid4().hex[:20], title="Опрос"
        )
        session.add(template)
        await session.flush()
        survey = SurveySession(
            id=uuid4(),
            candidate_id=UUID(candidate_id),
            survey_id=template.id,
            status=enums.SessionStatus.SCORED,
            specialization_id=spec.id,
            claimed_grade_id=grade.id,
        )
        session.add(survey)
        await session.commit()
        return str(survey.id)


async def test_upgraded_result_applied_once_as_claimed_and_blocks_retry(client):
    headers, user_id = await account(client)
    survey_id = await prepared_survey(client, user_id)
    remote_id, task_id = str(uuid4()), str(uuid4())
    score_calls = []
    result = {
        "session_id": remote_id,
        "claimed_grade": "middle",
        "assigned_grade": "senior",
        "outcome": "upgraded",
        "levels": [{"grade": "middle", "confirmed": True}],
        "score": {"score": 0.85},
        "tasks": [],
    }

    def remote(request):
        if request.url.path == "/sessions":
            return httpx.Response(
                201,
                json={
                    "id": remote_id,
                    "candidate_ref": user_id,
                    "area": "backend",
                    "claimed_grade": "middle",
                    "status": "ready",
                },
            )
        if request.url.path.endswith("/result"):
            return httpx.Response(409 if not score_calls else 200, json=result)
        if request.url.path.endswith("/score"):
            score_calls.append(1)
            return httpx.Response(200, json=result)
        return httpx.Response(
            200,
            json={
                "id": remote_id,
                "candidate_ref": user_id,
                "area": "backend",
                "claimed_grade": "middle",
                "status": "ready",
                "tasks_issued": True,
                "tasks": [
                    {
                        "id": task_id,
                        "status": "ready",
                        "kind": "code",
                        "statement": "Задание",
                        "public": {},
                    }
                ],
            },
        )

    client.app.state.settings.taskgen_client_key = "test-client"
    original = client.app.state.http
    client.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    try:
        response = await client.post(
            "/tests",
            headers={**headers, "Idempotency-Key": "test-create-123"},
            json={"survey_session_id": survey_id},
        )
        assert response.status_code == 202, response.text
        operation = response.json()
        await process_operation(client.app, UUID(operation["id"]))
        local_id = operation["resource_id"]
        response = await client.get(f"/tests/{local_id}", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["tasks"][0]["id"] == task_id
        finish = await client.post(f"/tests/{local_id}/finish", headers=headers)
        assert finish.status_code == 202, finish.text
        await process_operation(client.app, UUID(finish.json()["id"]))
        second = await client.post(f"/tests/{local_id}/finish", headers=headers)
        assert second.status_code == 202, second.text
        assert len(score_calls) == 1
        async with client.app.state.sessionmaker() as session:
            assignments = (
                await session.scalars(
                    select(GradeAssignment).where(GradeAssignment.test_session_id == UUID(local_id))
                )
            ).all()
            assert len(assignments) == 1
            local = await session.get(Attempt, UUID(local_id))
            assert float(local.total_score) == 0.85
        retry = await client.post(
            "/tests",
            headers={**headers, "Idempotency-Key": "new-create-123"},
            json={"survey_session_id": survey_id},
        )
        assert retry.status_code == 409, retry.text
    finally:
        await client.app.state.http.aclose()
        client.app.state.http = original


async def test_unknown_remote_create_is_not_retried(client):
    headers, user_id = await account(client)
    survey_id = await prepared_survey(client, user_id)
    calls = []
    remote_id = str(uuid4())

    def remote(request):
        if request.method == "GET" and request.url.path.startswith("/requests/"):
            return httpx.Response(200, json={"session_id": remote_id})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "id": remote_id,
                    "candidate_ref": user_id,
                    "area": "backend",
                    "claimed_grade": "middle",
                    "status": "ready",
                },
            )
        calls.append(request)
        raise httpx.ReadTimeout("Ответ потерян", request=request)

    client.app.state.settings.taskgen_client_key = "test-client"
    original = client.app.state.http
    client.app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    try:
        response = await client.post(
            "/tests",
            headers={**headers, "Idempotency-Key": "uncertain-create"},
            json={"survey_session_id": survey_id},
        )
        operation_id = UUID(response.json()["id"])
        await process_operation(client.app, operation_id)
        await process_operation(client.app, operation_id)
        operation = await client.get(f"/operations/{operation_id}", headers=headers)
        assert operation.json()["state"] == "succeeded"
        assert len(calls) == 1
    finally:
        await client.app.state.http.aclose()
        client.app.state.http = original
