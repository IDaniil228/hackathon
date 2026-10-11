from uuid import UUID, uuid4

from app import operations


async def test_processing_revocation_queues_owned_vector_erasure(client, monkeypatch):
    email = f"erase-{uuid4()}@example.com"
    account = await client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "test-map-erasure-password",
            "roles": ["candidate"],
            "first_name": "Мария",
            "last_name": "Пример",
            "accept_processing": True,
        },
    )
    uid = account.json()["id"]
    login = await client.post(
        "/auth/login", json={"email": email, "password": "test-map-erasure-password"}
    )
    client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
    calls = []

    async def service(_app, name, method, path, **kwargs):
        calls.append((name, method, path, kwargs))
        return {"status": "done"}

    monkeypatch.setattr(operations, "call_service", service)
    client.app.state.settings.matcher_client_key = "erasure-client"
    revoked = await client.post("/me/consents", json={"kind": "pd_processing", "granted": False})
    assert revoked.status_code == 200
    operation_id = UUID(revoked.json()["cleanup_operation_id"])
    await operations.process_operation(client.app, operation_id)
    result = await client.get(f"/operations/{operation_id}")
    assert result.json()["state"] == "succeeded"
    assert len(calls) == 1
    assert calls[0][:3] == ("matcher", "POST", f"/candidates/{uid}/maps/revoke")
    assert calls[0][3]["json"]["before"]
    assert not (await client.get("/me/profile")).json()["privacy"]["is_searchable"]
