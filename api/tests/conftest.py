import os

import httpx
import pytest

from app.main import create_app
from app.settings import Settings


@pytest.fixture
async def client(tmp_path):
    url = os.environ.get("API_TEST_DATABASE_URL")
    if not url:
        pytest.fail("Задайте API_TEST_DATABASE_URL отдельной тестовой БД")
    app = create_app(
        Settings(
            database_url=url,
            jwt_secret="test-secret-" * 5,
            demo_auth_allow_unverified=True,
            demo_email_tokens=True,
            cookie_secure=False,
            storage_dir=tmp_path,
            fsp_demo_enabled=True,
            allow_public_contacts=True,
        )
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            client.app = app
            yield client
