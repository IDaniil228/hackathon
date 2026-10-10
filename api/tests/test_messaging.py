import asyncio
from uuid import uuid4

from test_disclosures import prepare


async def opened(client):
    candidate, candidate_id, employer = await prepare(client)
    invitation = await client.post(
        "/invitations",
        headers=employer,
        json={
            "candidate_id": candidate_id,
            "message": "Хотим обсудить работу",
            "salary_min": 100000,
            "salary_max": 200000,
            "contact_method": "platform",
        },
    )
    assert invitation.status_code == 201, invitation.text
    response = await client.post(
        f"/invitations/{invitation.json()['id']}/conversation", headers=employer
    )
    assert response.status_code == 201, response.text
    return candidate, candidate_id, employer, invitation.json(), response.json()


async def test_employer_writes_before_accept_and_reply_does_not_reveal_contacts(client):
    candidate, candidate_id, employer, invitation, conversation = await opened(client)
    conversation_id = conversation["id"]
    initial = await client.get(f"/conversations/{conversation_id}/messages", headers=candidate)
    assert initial.status_code == 200, initial.text
    assert initial.json()["results"][0]["content"] == invitation["message"]
    assert initial.json()["results"][0]["kind"] == "offer"
    assert conversation["offer"]["salary_min"] == 100000
    assert conversation["offer"]["salary_max"] == 200000
    sent = await client.post(
        f"/conversations/{conversation_id}/messages",
        headers=employer,
        json={"content": "Удобно поговорить завтра?", "client_message_id": str(uuid4())},
    )
    assert sent.status_code == 201, sent.text
    reply = await client.post(
        f"/conversations/{conversation_id}/messages",
        headers=candidate,
        json={"content": "Да, расскажите про команду", "client_message_id": str(uuid4())},
    )
    assert reply.status_code == 201, reply.text
    assert (await client.get(f"/invitations/{invitation['id']}", headers=employer)).json()[
        "status"
    ] == "sent"
    assert (
        "contacts" not in (await client.get(f"/candidates/{candidate_id}", headers=employer)).json()
    )


async def test_other_users_cannot_read_or_send_messages(client):
    candidate, _, employer, _, conversation = await opened(client)
    other_candidate, _, other_employer, _, other_conversation = await opened(client)
    path = f"/conversations/{conversation['id']}"
    for headers in (other_candidate, other_employer):
        assert (await client.get(path, headers=headers)).status_code == 404
        assert (await client.get(path + "/messages", headers=headers)).status_code == 404
        assert (
            await client.post(
                path + "/messages",
                headers=headers,
                json={"content": "Чужой текст", "client_message_id": str(uuid4())},
            )
        ).status_code == 404
    listed = (await client.get("/conversations", headers=candidate)).json()["results"]
    assert conversation["id"] in {item["id"] for item in listed}
    assert other_conversation["id"] not in {item["id"] for item in listed}


async def test_message_replay_read_cursor_and_close(client):
    candidate, _, employer, invitation, conversation = await opened(client)
    path = f"/conversations/{conversation['id']}"
    repeated = await client.post(f"/invitations/{invitation['id']}/conversation", headers=candidate)
    assert repeated.json()["id"] == conversation["id"]
    payload = {"content": "Сообщение", "client_message_id": str(uuid4())}
    responses = await asyncio.gather(
        *(client.post(path + "/messages", headers=employer, json=payload) for _ in range(2))
    )
    assert all(response.status_code == 201 for response in responses)
    assert responses[0].json()["id"] == responses[1].json()["id"]
    assert (
        await client.post(
            path + "/messages", headers=employer, json={**payload, "content": "Другой текст"}
        )
    ).status_code == 409
    assert (await client.get(path, headers=candidate)).json()["unread_count"] == 2
    assert (await client.post(path + "/read", headers=candidate, json={})).status_code == 200
    assert (await client.get(path, headers=candidate)).json()["unread_count"] == 0
    assert (await client.post(path + "/close", headers=candidate)).status_code == 200
    assert (
        await client.post(
            path + "/messages",
            headers=employer,
            json={"content": "Ещё текст", "client_message_id": str(uuid4())},
        )
    ).status_code == 409
    assert (await client.get(path + "/messages", headers=candidate)).status_code == 200


async def test_declined_invitation_stops_new_messages(client):
    candidate, _, employer, invitation, conversation = await opened(client)
    await client.post(f"/invitations/{invitation['id']}/decline", headers=candidate)
    response = await client.post(
        f"/conversations/{conversation['id']}/messages",
        headers=employer,
        json={"content": "Продолжим?", "client_message_id": str(uuid4())},
    )
    assert response.status_code == 409
