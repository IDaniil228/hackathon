from uuid import uuid4

from test_api import account


async def prepare(client):
    candidate, candidate_id = await account(client)
    await client.post(
        "/me/consents", headers=candidate, json={"kind": "profile_publication", "granted": True}
    )
    await client.put(
        "/me/privacy", headers=candidate, json={"show_contacts": True, "is_searchable": True}
    )
    employer, _ = await account(client, "employer")
    await client.post(
        "/companies", headers=employer, json={"name": "Компания контактов", "full_name": "Рекрутер"}
    )
    return candidate, candidate_id, employer


async def test_accepted_invitation_remains_basis_after_application_withdrawal(client):
    candidate, candidate_id, employer = await prepare(client)
    categories = (await client.get("/references/categories")).json()
    vacancy = await client.post(
        "/vacancies",
        headers=employer,
        json={
            "title": "Вакансия",
            "description": "Описание",
            "category_id": categories[0]["id"],
            "salary_min": 100000,
            "salary_max": 200000,
        },
    )
    assert vacancy.status_code == 201, vacancy.text
    vacancy_id = vacancy.json()["id"]
    await client.post(
        f"/vacancies/{vacancy_id}/status", headers=employer, json={"status": "published"}
    )
    application = await client.post(
        f"/vacancies/{vacancy_id}/applications", headers=candidate, json={}
    )
    invitation = await client.post(
        "/invitations",
        headers=employer,
        json={
            "candidate_id": candidate_id,
            "message": "Предложение",
            "salary_min": 100000,
            "salary_max": 200000,
            "contact_method": "email",
            "contact_value": "hr@example.org",
        },
    )
    assert invitation.status_code == 201, invitation.text
    await client.post(f"/invitations/{invitation.json()['id']}/accept", headers=candidate)
    await client.post(f"/applications/{application.json()['id']}/withdraw", headers=candidate)
    assert "contacts" in (await client.get(f"/candidates/{candidate_id}", headers=employer)).json()


async def test_invitation_create_replay_is_one_offer(client):
    _, candidate_id, employer = await prepare(client)
    payload = {
        "candidate_id": candidate_id,
        "message": "Предложение",
        "salary_min": 100000,
        "salary_max": 200000,
        "contact_method": "email",
        "contact_value": "hr@example.org",
    }
    headers = {**employer, "Idempotency-Key": uuid4().hex}
    first = await client.post("/invitations", headers=headers, json=payload)
    second = await client.post("/invitations", headers=headers, json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    changed = await client.post(
        "/invitations", headers=headers, json={**payload, "salary_max": 250000}
    )
    assert changed.status_code == 409
