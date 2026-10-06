import os

import pytest
from fastapi.testclient import TestClient

from taskgen.app import create_app
from taskgen.config import GenerationSettings, SelectionSettings, Settings

ADMIN_KEY = "test-admin-key"
DATABASE_URL = os.environ.get(
    "TASKGEN_DATABASE_URL", "postgresql+asyncpg://taskgen:taskgen@localhost:5432/taskgen"
)


@pytest.fixture
def settings() -> Settings:
    # Без autostart приложение при старте не подхватывает чужие сессии из общей тестовой БД,
    # а с пустым пулом импорт шаблонов не запускает генерацию: тесты подменяют LLM уже после
    # старта, и иначе фоновая работа пошла бы в настоящую модель.
    # Тест сессии кладёт в свою область один-два шаблона нужных ему типов. Без квоты и с
    # запасом по числу заданий в сессию идут все они, а типов, которых нет, просто нет.
    # Квоту и состав по типам проверяют тесты, которые задают их явно.
    return Settings(
        database_url=DATABASE_URL,
        bootstrap_admin_key=ADMIN_KEY,
        generation=GenerationSettings(autostart=False, pool_size=0),
        selection=SelectionSettings(
            code_tasks=5, essay_tasks=5, quiz_tasks=5, min_claimed_per_kind=0
        ),
    )


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client
