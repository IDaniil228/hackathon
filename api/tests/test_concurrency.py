import asyncio
from uuid import UUID

from sqlalchemy import select
from test_api import account

from app.db.models.hire import ContactDisclosure, InvitationEvent


async def test_parallel_refresh_uses_old_token_only_once(client):
    await account(client)
    csrf = client.cookies.get("fsp_csrf")
    requests = [
        client.build_request("POST", "/auth/refresh", headers={"X-CSRF-Token": csrf})
        for _ in range(2)
    ]
    responses = await asyncio.gather(*(client.send(request) for request in requests))
    assert sorted(response.status_code for response in responses) == [200, 401]


async def test_parallel_accept_creates_one_event_and_one_disclosure(client):
    candidate, candidate_id = await account(client)
    await client.post(
        "/me/consents", headers=candidate, json={"kind": "profile_publication", "granted": True}
    )
    await client.put(
        "/me/privacy", headers=candidate, json={"show_contacts": True, "is_searchable": True}
    )
    employer, _ = await account(client, "employer")
    company = await client.post(
        "/companies", headers=employer, json={"name": "Компания гонок", "full_name": "Рекрутер"}
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
    invitation_id = invitation.json()["id"]
    responses = await asyncio.gather(
        *(client.post(f"/invitations/{invitation_id}/accept", headers=candidate) for _ in range(2))
    )
    assert [response.status_code for response in responses] == [200, 200]
    async with client.app.state.sessionmaker() as session:
        events = (
            await session.scalars(
                select(InvitationEvent).where(
                    InvitationEvent.invitation_id == UUID(invitation_id),
                    InvitationEvent.to_status == "accepted",
                )
            )
        ).all()
        disclosures = (
            await session.scalars(
                select(ContactDisclosure).where(
                    ContactDisclosure.candidate_id == UUID(candidate_id),
                    ContactDisclosure.company_id == UUID(company.json()["id"]),
                    ContactDisclosure.revoked_at.is_(None),
                )
            )
        ).all()
        assert len(events) == len(disclosures) == 1


async def test_validation_errors_do_not_echo_password(client):
    secret = "shortsecret"
    response = await client.post(
        "/auth/register",
        json={
            "email": "validation@example.org",
            "password": secret,
            "roles": ["candidate"],
            "first_name": "Иван",
            "last_name": "Тестов",
            "accept_processing": True,
        },
    )
    assert response.status_code == 422
    assert secret not in response.text
