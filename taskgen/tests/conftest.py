import os

import pytest
from fastapi.testclient import TestClient

from taskgen.app import create_app
from taskgen.config import Settings

ADMIN_KEY = "test-admin-key"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=os.environ.get(
            "TASKGEN_DATABASE_URL", "postgresql+asyncpg://taskgen:taskgen@localhost:5432/taskgen"
        ),
        bootstrap_admin_key=ADMIN_KEY,
    )


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client
