"""Настройки приложения. Читаются из переменных окружения, их задаёт .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # psycopg 3 обслуживает оба режима: sync для Alembic, async для FastAPI
    database_url: str = "postgresql+psycopg://fsp:change_me_locally@localhost:5432/fsp"

    echo_sql: bool = False

    @property
    def async_database_url(self) -> str:
        return self.database_url

    @property
    def sync_database_url(self) -> str:
        """URL для Alembic: тот же драйвер, но без асинхронного движка."""
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
