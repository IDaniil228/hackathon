"""Настройки сервиса.

Это единственное место, где заданы значения по умолчанию и смысл настроек, поэтому у каждого
поля есть description. Любое поле переопределяется переменной окружения с префиксом
MATCHER_, вложенное поле — через двойное подчёркивание, например MATCHER_VECTOR__TOP_K=40.
Провайдеры и механика ролей моделей взяты из копии taskgen в matcher.llm.llm_config.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from matcher.llm.llm_config import (
    CLOUD_MODEL,
    ENV_DELIMITER,
    LOCAL_MODEL,
    ChatRole,
    EvidenceSettings,
    LLMSettingsBase,
    ModelRole,
    Section,
)

ENV_PREFIX = "MATCHER_"


class LLMSettings(LLMSettingsBase):
    """Провайдеры моделей и роли матчера. Роль называет модель строкой провайдер/модель.

    Модели по умолчанию нет: каждый вызов матчера называет модель своей роли.
    """

    env_prefix = ENV_PREFIX

    rewriter: ChatRole = Field(
        default=ChatRole(model=LOCAL_MODEL),
        description="Переписывает потребность в карточку идеального кандидата по шаблону "
        "карточки (HyDE); вектор этой карточки ищет кандидатов",
    )
    ranker: ChatRole = Field(
        default=ChatRole(model=CLOUD_MODEL),
        description="Точечно оценивает кандидатов короткого списка: потребность с негативным "
        "промтом, карточка, machine summary, негативный промт кандидата и строки факторов",
    )
    checker: ChatRole = Field(
        default=ChatRole(model=CLOUD_MODEL),
        description="Сверяет объяснение выдачи и обзор прогона со строками факторов",
    )
    embedding: ModelRole = Field(
        default=ModelRole(model="ollama/bge-m3"),
        description="Модель эмбеддингов карточек кандидатов и потребностей. Векторы в БД "
        "помечены именем модели без провайдера, как в taskgen: смена провайдера той же модели "
        "не требует пересчёта. Размерность вектора задана типом колонки в схеме api "
        "(match.candidate_embeddings), и сервис сверяет её с моделью при старте",
    )


class VectorSettings(Section):
    """Векторная стадия: косинусная близость карточки запроса к карточкам кандидатов пула."""

    top_k: int = Field(
        default=30,
        ge=1,
        description="Сколько ближайших кандидатов пула идут дальше. Вектор отвечает за "
        "полноту, а порядок задают следующие стадии. Если пул после префильтра не больше "
        "top_k, векторная стадия пропускается и в params прогона пишется vector_skipped",
    )


class ExplorationSettings(Section):
    """Разведка: малая фиксированная доля профилей вне векторного top_k в коротком списке.

    Она даёт шанс новым профилям, у которых ещё нет истории, и показывает в валидации, что
    вектор отсекает. Случайный выбор воспроизводим: генератор берёт seed от id прогона.
    """

    fresh: int = Field(
        default=3, ge=0, description="Сколько самых новых профилей пула вне top_k добавить"
    )
    random: int = Field(
        default=3,
        ge=0,
        description="Сколько случайных профилей из оставшихся вне top_k добавить",
    )


class LLMStageSettings(Section):
    """LLM-стадия: HyDE-карточка запроса и точечная оценка короткого списка."""

    enabled: bool = Field(
        default=False,
        description="Включает LLM-стадию. Выключенная стадия даёт порядок по факторам и "
        "объяснения по шаблону из строк факторов",
    )
    batch_size: int = Field(
        default=8, ge=1, description="Сколько кандидатов оценивает один вызов ранжировщика"
    )


class ExplanationSettings(Section):
    """Объяснение строки выдачи для работодателя."""

    rewrites: int = Field(
        default=2,
        ge=0,
        description="Сколько раз LLM переписывает объяснение, которое отклонили барьер или "
        "проверяльщик; после этого остаётся объяснение по шаблону из строк факторов",
    )


class TaskgenSettings(Section):
    """Сервис taskgen, источник machine summary кандидата для его карточки."""

    base_url: str | None = Field(
        default=None,
        description="Адрес taskgen, например http://taskgen:8000; null — карточки без "
        "machine summary. candidate_ref в taskgen равен profile.candidates.user_id",
    )
    api_key: SecretStr | None = Field(
        default=None,
        description="Ключ роли client в taskgen, уходит в Authorization: Bearer. Задаётся "
        "только переменной окружения",
    )
    timeout_seconds: float = Field(
        default=10.0, gt=0, description="Таймаут одного запроса к taskgen, секунды"
    )
    concurrency: int = Field(
        default=8, ge=1, description="Сколько запросов summary к taskgen идут одновременно"
    )


class EmbeddingSettings(Section):
    """Кэш векторов карточек в match.candidate_embeddings и match.need_embeddings."""

    batch_size: int = Field(
        default=32, ge=1, description="Сколько карточек уходит в один запрос к эмбеддеру"
    )


class ResultsSettings(Section):
    """Что прогон пишет в match.search_results."""

    store_pool: bool = Field(
        default=True,
        description="Писать и кандидатов пула, которые не прошли векторную стадию, со stage "
        "prefilter и vector_score, без ранга. Это след для валидации полноты вектора",
    )


class Settings(BaseSettings):
    """Корень настроек; переменные окружения и файл .env читаются при старте."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_nested_delimiter=ENV_DELIMITER,
        env_file=".env",
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql+asyncpg://matcher:matcher@localhost:5432/fsp",
        description="Адрес базы api в формате SQLAlchemy asyncpg под ролью matcher: чтение "
        "ref, profile, assess и hire, запись в match, свои таблицы в схеме matcher",
    )
    bootstrap_admin_key: str | None = Field(
        default=None,
        description="Первый admin-ключ; остальные ключи создаются через API и хранятся хэшами",
    )

    llm: LLMSettings = Field(default=LLMSettings(), description=LLMSettings.__doc__)
    evidence: EvidenceSettings = Field(
        default=EvidenceSettings(), description=EvidenceSettings.__doc__
    )
    vector: VectorSettings = Field(default=VectorSettings(), description=VectorSettings.__doc__)
    exploration: ExplorationSettings = Field(
        default=ExplorationSettings(), description=ExplorationSettings.__doc__
    )
    llm_stage: LLMStageSettings = Field(
        default=LLMStageSettings(), description=LLMStageSettings.__doc__
    )
    explanation: ExplanationSettings = Field(
        default=ExplanationSettings(), description=ExplanationSettings.__doc__
    )
    taskgen: TaskgenSettings = Field(default=TaskgenSettings(), description=TaskgenSettings.__doc__)
    embeddings: EmbeddingSettings = Field(
        default=EmbeddingSettings(), description=EmbeddingSettings.__doc__
    )
    results: ResultsSettings = Field(default=ResultsSettings(), description=ResultsSettings.__doc__)


@lru_cache
def get_settings() -> Settings:
    return Settings()
