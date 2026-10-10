"""Настройки приложения. Читаются из переменных окружения, их задаёт .env."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.resume_parser import ResumeLLMSettings


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_nested_delimiter="__")

    # psycopg 3 обслуживает оба режима: sync для Alembic, async для FastAPI
    database_url: str = "postgresql+psycopg://fsp:change_me_locally@localhost:5432/fsp"

    echo_sql: bool = False
    jwt_secret: str = ""
    jwt_issuer: str = "fsp-api"
    access_token_minutes: int = 15
    refresh_token_days: int = 7
    demo_auth_allow_unverified: bool = False
    allow_public_contacts: bool = False
    cookie_secure: bool = True
    cors_origins: list[str] = []
    taskgen_url: str = "http://localhost:8001"
    taskgen_client_key: str = ""
    matcher_url: str = "http://localhost:8002"
    matcher_client_key: str = ""
    service_timeout_seconds: float = 120
    auth_requests_per_minute: int = 100
    storage_dir: Path = Path(".storage")
    brand_dir: Path = Path(__file__).resolve().parents[2] / "brand"
    fsp_demo_enabled: bool = False
    demo_email_tokens: bool = False
    resume_llm_enabled: bool = False
    llm: ResumeLLMSettings = Field(default_factory=ResumeLLMSettings)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    smtp_sender: str = ""
    frontend_url: str = "http://localhost:3000"

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
