import pytest

from taskgen.app import api_routes, create_app
from taskgen.auth import Role, route_roles
from tests.conftest import ADMIN_KEY


def test_every_keyed_route_documents_auth_errors(settings):
    app = create_app(settings)
    paths = app.openapi()["paths"]
    routes = list(api_routes(app.routes))
    keyed = [route for route in routes if route_roles(route) is not None]

    missing = [
        (method, route.path)
        for route in keyed
        for method in route.methods
        if not {"401", "403"} <= paths[route.path][method.lower()]["responses"].keys()
    ]
    # Все эндпоинты, кроме /health, требуют ключ.
    assert len(keyed) == len(routes) - 1 and not missing, missing
    assert route_roles(next(r for r in routes if r.path == "/health")) is None
    assert route_roles(next(r for r in routes if r.path == "/keys")) == (Role.ADMIN,)


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
