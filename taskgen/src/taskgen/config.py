"""Настройки сервиса.

Это единственное место, где заданы значения по умолчанию: автодокументация берёт их отсюда.
Любое поле переопределяется переменной окружения с префиксом TASKGEN_, вложенное поле —
через двойное подчёркивание, например TASKGEN_SCORING__PASS_THRESHOLD=0.7.
"""

from functools import lru_cache

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseModel):
    """OpenAI-совместимый провайдер: Ollama, vLLM или облачный шлюз."""

    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    generator_model: str = "qwen3.5:9b"
    # Судья другой модели снижает завышение оценок собственного текста генератора.
    judge_model: str = "qwen3.5:4b"
    embedding_model: str = "bge-m3"
    timeout_seconds: float = 120.0


class SelectionSettings(BaseModel):
    """Отбор шаблонов: p ∝ exp(−d / T), d = alpha·|Δгрейд| + beta·(1 − сходство).

    |Δгрейд| — разность позиций грейдов в списке, упорядоченном по level, а не разность level.
    """

    # Шкала 0..1 переводится в T = t / (1 − t): 0 — только ближайшие, 1 — равномерно.
    temperature: float = Field(default=0.3, ge=0.0, le=1.0)
    alpha: float = Field(default=1.0, ge=0.0)
    beta: float = Field(default=1.0, ge=0.0)
    tasks_per_test: int = Field(default=5, ge=1)


class ScoringSettings(BaseModel):
    """Балл за задание с кодом — взвешенная сумма компонентов из диапазона 0..1."""

    weight_tests: float = 0.6
    weight_rubric: float = 0.25
    weight_quality: float = 0.1
    weight_performance: float = 0.05
    # Доля решённых заданий уровня, начиная с которой уровень считается подтверждённым.
    pass_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    judge_samples: int = Field(default=3, ge=1)


class SandboxSettings(BaseModel):
    piston_url: str = "http://localhost:2000"
    run_timeout_ms: int = 3000
    run_memory_limit_bytes: int = 256 * 1024 * 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TASKGEN_", env_nested_delimiter="__", env_file=".env", extra="ignore"
    )

    database_url: str = "postgresql+asyncpg://taskgen:taskgen@localhost:5432/taskgen"
    # Первый admin-ключ; остальные ключи создаются через API и хранятся хэшами.
    bootstrap_admin_key: str | None = None
    ai_detector_enabled: bool = False

    llm: LLMSettings = LLMSettings()
    selection: SelectionSettings = SelectionSettings()
    scoring: ScoringSettings = ScoringSettings()
    sandbox: SandboxSettings = SandboxSettings()


@lru_cache
def get_settings() -> Settings:
    return Settings()
