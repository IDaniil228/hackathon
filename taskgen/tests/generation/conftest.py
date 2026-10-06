import pytest
from fastapi.testclient import TestClient

from taskgen.app import create_app
from tests.generation.fakes import GatedLLM
from tests.generation.world import ADMIN, fake_world


@pytest.fixture
def world(settings):
    with TestClient(create_app(settings)) as client:
        w = fake_world(client)
        try:
            yield w
        finally:
            # Задержанные вызовы LLM отпускаются, иначе фоновая генерация ждала бы вечно.
            if isinstance(w.state.llm, GatedLLM):
                w.open(w.state.llm)
            w.wait()
            w.cleanup()


@pytest.fixture
def client_headers(world) -> dict[str, str]:
    issued = world.client.post("/keys", json={"name": "backend", "role": "client"}, headers=ADMIN)
    assert issued.status_code == 201
    return {"Authorization": f"Bearer {issued.json()['key']}"}
