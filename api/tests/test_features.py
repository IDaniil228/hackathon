from io import BytesIO
from uuid import UUID

from pypdf import PdfReader
from test_api import account
from test_services import prepared_survey

from app.db.models.assess import SurveySession


async def test_public_contacts_need_explicit_consent_and_global_mode(client):
    candidate, user_id = await account(client)
    await client.post(
        "/me/consents", headers=candidate, json={"kind": "profile_publication", "granted": True}
    )
    await client.put("/me/privacy", headers=candidate, json={"is_searchable": True})
    assert (
        await client.put("/me/privacy", headers=candidate, json={"public_contacts": True})
    ).status_code == 409
    response = await client.put(
        "/me/privacy",
        headers=candidate,
        json={"public_contacts": True, "accept_public_contacts": True},
    )
    assert response.status_code == 200, response.text
    employer, _ = await account(client, "employer")
    await client.post(
        "/companies", headers=employer, json={"name": "Публичная компания", "full_name": "Рекрутер"}
    )
    assert "contacts" in (await client.get(f"/candidates/{user_id}", headers=employer)).json()
    client.app.state.settings.allow_public_contacts = False
    assert "contacts" not in (await client.get(f"/candidates/{user_id}", headers=employer)).json()
    await client.post(
        "/me/consents", headers=candidate, json={"kind": "profile_publication", "granted": False}
    )
    assert (await client.get(f"/candidates/{user_id}", headers=employer)).status_code == 404


async def test_email_token_is_single_use_and_activates_ordinary_mode(client):
    headers, _ = await account(client)
    response = await client.post("/auth/email/request", headers=headers)
    assert response.status_code == 202, response.text
    token = response.json()["token"]
    client.app.state.settings.demo_auth_allow_unverified = False
    identity = await client.get("/me", headers=headers)
    assert identity.status_code == 200
    assert identity.json()["email_verified"] is False
    assert (await client.get("/me/profile", headers=headers)).status_code == 403
    assert (await client.post("/auth/email/verify", json={"token": token})).status_code == 200
    assert (await client.get("/me", headers=headers)).status_code == 200
    assert (await client.get("/me/profile", headers=headers)).status_code == 200
    assert (await client.post("/auth/email/verify", json={"token": token})).status_code == 422


async def test_fsp_link_empty_history_sync_and_code_replay(client):
    headers, _ = await account(client)
    await client.post(
        "/me/consents", headers=headers, json={"kind": "fsp_data_sync", "granted": True}
    )
    code = await client.post("/me/fsp/demo-code", headers=headers, json={"history": "empty"})
    assert code.status_code == 200, code.text
    linked = await client.post("/me/fsp/link", headers=headers, json={"code": code.json()["code"]})
    assert linked.status_code == 200, linked.text
    assert linked.json()["demo"] is True
    assert (
        await client.post("/me/fsp/link", headers=headers, json={"code": code.json()["code"]})
    ).status_code == 422
    profile = await client.get("/me/profile", headers=headers)
    assert profile.json()["fsp_achievements"] == []
    code = await client.post("/me/fsp/demo-code", headers=headers, json={"history": "team_prize"})
    assert (
        await client.post("/me/fsp/link", headers=headers, json={"code": code.json()["code"]})
    ).status_code == 200
    assert (await client.post("/me/fsp/sync", headers=headers)).status_code == 200
    achievements = (await client.get("/me/profile", headers=headers)).json()["fsp_achievements"]
    assert len(achievements) == 1 and achievements[0]["demo"] is True
    await client.delete("/me/fsp/link", headers=headers)
    assert (await client.get("/me/profile", headers=headers)).json()["fsp_achievements"] == []


async def test_resume_proposals_require_confirmation_and_raw_is_owner_only(client):
    headers, user_id = await account(client)
    await client.put(
        "/me/skills", headers=headers, json={"skills": [{"skill_id": 1, "self_level": 3}]}
    )
    pdf = await client.get("/me/profile.pdf", headers=headers)
    assert pdf.status_code == 200, pdf.text if pdf.status_code != 200 else ""
    upload = await client.post(
        "/me/resumes",
        headers=headers,
        files={"file": ("profile.pdf", pdf.content, "application/pdf")},
    )
    assert upload.status_code == 201, upload.text
    value = upload.json()
    assert value["parse"]["applied_at"] is None
    assert (
        await client.post(
            f"/me/resume-parses/{value['parse']['id']}/apply",
            headers=headers,
            json={"accept_fields": [], "accept_skill_ids": value["parse"]["payload"]["skill_ids"]},
        )
    ).status_code == 200
    other, _ = await account(client)
    assert (
        await client.get(f"/me/resumes/{value['document']['id']}", headers=other)
    ).status_code == 404
    await client.post(
        "/me/consents", headers=headers, json={"kind": "profile_publication", "granted": True}
    )
    await client.put("/me/privacy", headers=headers, json={"is_searchable": True})
    employer, _ = await account(client, "employer")
    await client.post(
        "/companies", headers=employer, json={"name": "Компания PDF", "full_name": "Рекрутер"}
    )
    employer_pdf = await client.get(f"/candidates/{user_id}/profile.pdf", headers=employer)
    assert employer_pdf.status_code == 200
    text = " ".join(page.extract_text() for page in PdfReader(BytesIO(employer_pdf.content)).pages)
    email = (await client.get("/me", headers=headers)).json()["email"]
    assert email not in text
    assert email in " ".join(page.extract_text() for page in PdfReader(BytesIO(pdf.content)).pages)


async def test_manual_assignment_updates_freshness_not_grade(client):
    candidate, candidate_id = await account(client)
    survey_id = await prepared_survey(client, candidate_id)
    async with client.app.state.sessionmaker() as session:
        survey = await session.get(SurveySession, UUID(survey_id))
        data = {
            "specialization_id": survey.specialization_id,
            "claimed_grade_id": survey.claimed_grade_id,
            "answers": [],
        }
        template_id = survey.survey_id
    response = await client.post(f"/surveys/{template_id}/submit", headers=candidate, json=data)
    assert response.status_code == 201, response.text
    await client.post(
        "/me/consents", headers=candidate, json={"kind": "profile_publication", "granted": True}
    )
    await client.put("/me/privacy", headers=candidate, json={"is_searchable": True})
    employer, _ = await account(client, "employer")
    await client.post(
        "/companies", headers=employer, json={"name": "Компания заданий", "full_name": "Рекрутер"}
    )
    assignment = await client.post(
        "/assignments",
        headers=employer,
        json={
            "candidate_id": candidate_id,
            "title": "Короткая задача",
            "statement": "Предложите схему API",
            "max_score": 10,
        },
    )
    assert assignment.status_code == 201, assignment.text
    assignment_id = assignment.json()["id"]
    assert (
        await client.post(
            f"/assignments/{assignment_id}/answer", headers=candidate, json={"answer": "Решение"}
        )
    ).status_code == 200
    assert (
        await client.post(
            f"/assignments/{assignment_id}/score",
            headers=employer,
            json={"score": 8, "feedback": "Хороший ответ"},
        )
    ).status_code == 200
    profile = (await client.get("/me/profile", headers=candidate)).json()
    assert profile["grade_confirmed"] is False
    assert profile["profile"]["profile_refreshed_at"] is not None
