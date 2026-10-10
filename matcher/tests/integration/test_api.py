"""HTTP API матчера: ключи, запуск прогона и его статус."""

import asyncio
import time
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from matcher.app import create_app
from matcher.config import EmbeddingSettings, Settings, VectorSettings
from matcher.db import RunStatus, make_engine
from tests.conftest import FakeEmbedder
from tests.integration.conftest import DEMO_NEED, purge_embeddings

ADMIN_KEY = "test-admin-key"


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    settings = Settings(
        database_url=database_url,
        bootstrap_admin_key=ADMIN_KEY,
        vector=VectorSettings(top_k=2),
    )
    created: list[str] = []
    with TestClient(create_app(settings, embedder=FakeEmbedder())) as test_client:
        test_client.created_runs = created
        yield test_client

    async def purge() -> None:
        engine = make_engine(database_url)
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM match.search_runs WHERE id = ANY(:ids)"),
                {"ids": [uuid.UUID(run_id) for run_id in created]},
            )
            await connection.execute(text("DELETE FROM matcher.api_keys WHERE name LIKE 'test-%'"))
            await purge_embeddings(connection, FakeEmbedder().model_name)
        await engine.dispose()

    asyncio.run(purge())


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _client_key(client: TestClient) -> str:
    response = client.post(
        "/keys", json={"name": "test-api", "role": "client"}, headers=_headers(ADMIN_KEY)
    )
    assert response.status_code == 201, response.text
    return response.json()["key"]


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


class _SilentEmbedder(FakeEmbedder):
    """Эмбеддер, до которого запрос не доходит: так ведёт себя адрес, где порт не слушают."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        await asyncio.Event().wait()
        raise AssertionError("недостижимо")


def test_silent_embedder_does_not_block_startup(database_url: str) -> None:
    settings = Settings(
        database_url=database_url,
        embeddings=EmbeddingSettings(startup_check_seconds=0.5),
    )
    started = time.monotonic()
    with TestClient(create_app(settings, embedder=_SilentEmbedder())) as test_client:
        assert test_client.get("/health").json() == {"status": "ok"}
    assert time.monotonic() - started < 5


def test_keys_need_admin(client: TestClient) -> None:
    key = _client_key(client)
    assert client.post("/keys", json={"name": "test-x", "role": "client"}).status_code == 401
    forbidden = client.post(
        "/keys", json={"name": "test-x", "role": "admin"}, headers=_headers(key)
    )
    assert forbidden.status_code == 403


def test_run_is_accepted_and_finishes(client: TestClient) -> None:
    key = _client_key(client)
    assert client.post(f"/needs/{DEMO_NEED}/runs").status_code == 401
    response = client.post(f"/needs/{DEMO_NEED}/runs", headers=_headers(key))
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    client.created_runs.append(run_id)
    deadline = time.monotonic() + 30
    while True:
        run = client.get(f"/runs/{run_id}", headers=_headers(key)).json()
        if run["status"] in (RunStatus.DONE, RunStatus.FAILED) or time.monotonic() > deadline:
            break
        time.sleep(0.1)
    assert run["status"] == RunStatus.DONE, run
    assert run["need_id"] == DEMO_NEED
    assert [row["rank"] for row in run["results"]] == list(range(1, len(run["results"]) + 1))
    assert all(row["explanation"] and row["factors"] for row in run["results"])


def test_unknown_need_and_run_are_404(client: TestClient) -> None:
    key = _client_key(client)
    assert client.post(f"/needs/{uuid.uuid4()}/runs", headers=_headers(key)).status_code == 404
    assert client.get(f"/runs/{uuid.uuid4()}", headers=_headers(key)).status_code == 404


def test_request_reference_replays_run_and_is_scoped_to_client(client):
    key = _client_key(client)
    reference = str(uuid.uuid4())
    first = client.post(
        f"/needs/{DEMO_NEED}/runs", headers=_headers(key), json={"request_ref": reference}
    )
    assert first.status_code == 202, first.text
    client.created_runs.append(first.json()["run_id"])
    replay = client.post(
        f"/needs/{DEMO_NEED}/runs", headers=_headers(key), json={"request_ref": reference}
    )
    assert replay.status_code == 202 and replay.json()["run_id"] == first.json()["run_id"]
    lookup = client.get(f"/requests/{reference}/run", headers=_headers(key))
    assert lookup.status_code == 200 and lookup.json()["run_id"] == first.json()["run_id"]
    assert (
        client.post(
            f"/needs/{uuid.uuid4()}/runs", headers=_headers(key), json={"request_ref": reference}
        ).status_code
        == 409
    )
    other = _client_key(client)
    assert client.get(f"/requests/{reference}/run", headers=_headers(other)).status_code == 404
