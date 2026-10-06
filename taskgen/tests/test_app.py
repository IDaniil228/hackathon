import pytest

from tests.conftest import ADMIN_KEY


def test_health_answers_without_key(client):
    response = client.get("/health")
    assert response.status_code == 200


def test_protected_route_rejects_missing_key(client):
    response = client.post("/keys", json={"name": "x", "role": "client"})
    assert response.status_code == 401


@pytest.mark.integration
def test_protected_route_rejects_unknown_key(client):
    response = client.post(
        "/keys", json={"name": "x", "role": "client"}, headers={"Authorization": "Bearer nope"}
    )
    assert response.status_code == 401


@pytest.mark.integration
def test_admin_issues_key_and_client_key_cannot_issue(client):
    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    issued = client.post("/keys", json={"name": "backend", "role": "client"}, headers=admin)
    assert issued.status_code == 201
    client_key = issued.json()["key"]

    as_client = client.post(
        "/keys",
        json={"name": "escalate", "role": "admin"},
        headers={"Authorization": f"Bearer {client_key}"},
    )
    assert as_client.status_code == 403
