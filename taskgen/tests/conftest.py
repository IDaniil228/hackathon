import os

import pytest
from fastapi.testclient import TestClient

from taskgen.app import create_app
from taskgen.config import GenerationSettings, Settings

ADMIN_KEY = "test-admin-key"
DATABASE_URL = os.environ.get(
    "TASKGEN_DATABASE_URL", "postgresql+asyncpg://taskgen:taskgen@localhost:5432/taskgen"
)


@pytest.fixture
def settings() -> Settings:
    # Без autostart приложение при старте не подхватывает чужие сессии из общей тестовой БД
    # и не пополняет пул настоящей моделью: тесты подменяют LLM уже после старта.
    return Settings(
        database_url=DATABASE_URL,
        bootstrap_admin_key=ADMIN_KEY,
        generation=GenerationSettings(autostart=False),
    )


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client
