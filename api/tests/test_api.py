from uuid import uuid4


async def account(client, role="candidate"):
    email = f"{uuid4().hex}@example.org"
    response = await client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "test-password-123",
            "roles": [role],
            "first_name": "Иван",
            "last_name": "Тестов",
            "accept_processing": True,
        },
    )
    assert response.status_code == 201, response.text
    response = await client.post(
        "/auth/login", json={"email": email, "password": "test-password-123"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}, response.json()[
        "user_id"
    ]


async def test_registration_forbids_admin_and_extra_fields(client):
    response = await client.post(
        "/auth/register",
        json={
            "email": "admin@example.org",
            "password": "test-password-123",
            "roles": ["admin"],
            "first_name": "Иван",
            "last_name": "Тестов",
            "accept_processing": True,
        },
    )
    assert response.status_code == 422
    assert (await client.get("/me")).status_code == 401


async def test_candidate_cannot_read_other_candidate(client):
    a, _ = await account(client)
    _, b = await account(client)
    assert (await client.get(f"/candidates/{b}", headers=a)).status_code == 403


async def test_invitation_accept_reveals_contacts_only_to_sender(client):
    candidate, candidate_id = await account(client)
    consent = await client.post(
        "/me/consents", headers=candidate, json={"kind": "profile_publication", "granted": True}
    )
    assert consent.status_code == 200, consent.text
    response = await client.put(
        "/me/privacy", headers=candidate, json={"show_contacts": True, "is_searchable": True}
    )
    assert response.status_code == 200, response.text
    employer, _ = await account(client, "employer")
    company = await client.post(
        "/companies", headers=employer, json={"name": "Компания", "full_name": "Рекрутер"}
    )
    assert company.status_code == 201, company.text
    before = await client.get(f"/candidates/{candidate_id}", headers=employer)
    assert before.status_code == 200, before.text
    assert "contacts" not in before.json()
    response = await client.post(
        "/invitations",
        headers=employer,
        json={
            "candidate_id": candidate_id,
            "message": "Предложение",
            "salary_min": 100000,
            "salary_max": 150000,
            "contact_method": "email",
            "contact_value": "hr@example.org",
        },
    )
    assert response.status_code == 201, response.text
    invitation_id = response.json()["id"]
    response = await client.post(f"/invitations/{invitation_id}/accept", headers=candidate)
    assert response.status_code == 200, response.text
    assert (
        await client.post(f"/invitations/{invitation_id}/accept", headers=candidate)
    ).status_code == 200
    after = await client.get(f"/candidates/{candidate_id}", headers=employer)
    assert "contacts" in after.json()
    outsider, _ = await account(client, "employer")
    await client.post(
        "/companies",
        headers=outsider,
        json={"name": "Чужая компания", "full_name": "Другой рекрутер"},
    )
    assert (
        "contacts" not in (await client.get(f"/candidates/{candidate_id}", headers=outsider)).json()
    )
    assert (await client.get(f"/invitations/{invitation_id}", headers=outsider)).status_code == 404


async def test_self_report_cannot_confirm_skills(client):
    headers, _ = await account(client)
    response = await client.put(
        "/me/skills",
        headers=headers,
        json={"skills": [{"skill_id": 1, "self_level": 3, "confirmed_level": 5}]},
    )
    assert response.status_code == 422


async def test_logout_revokes_access(client):
    headers, _ = await account(client)
    csrf = client.cookies.get("fsp_csrf")
    assert (
        await client.post("/auth/logout", headers={**headers, "X-CSRF-Token": csrf})
    ).status_code == 200
    assert (await client.get("/me", headers=headers)).status_code == 401
