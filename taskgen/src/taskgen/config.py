"""Настройки сервиса.

Это единственное место, где заданы значения по умолчанию: автодокументация берёт их отсюда.
Любое поле переопределяется переменной окружения с префиксом TASKGEN_, вложенное поле —
через двойное подчёркивание, например TASKGEN_SCORING__PASS_THRESHOLD=0.7.
"""

from functools import lru_cache

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from taskgen.contracts import EssayCheckMethod, ReasoningEffort


class LLMSettings(BaseModel):
    """OpenAI-совместимый провайдер: Ollama, vLLM или облачный шлюз."""

    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    generator_model: str = "qwen3.5:9b"
    # Судья другой модели снижает завышение оценок собственного текста генератора.
    judge_model: str = "qwen3.5:4b"
    embedding_model: str = "bge-m3"
    # Поле reasoning_effort запроса для каждой роли; рядом с моделью, потому что уместное
    # значение зависит от модели. None — поле не передаётся, модель рассуждает как обычно.
    # На слабой машине "none" сокращает ответ в разы: рассуждение qwen3.5:4b на простой
    # запрос занимало тысячи токенов и иногда не оставляло места самому ответу.
    generator_reasoning_effort: ReasoningEffort | None = None
    judge_reasoning_effort: ReasoningEffort | None = None
    # Проверяльщик ситуации эссе: ищет в ней факты шаблона и цитирует их. None — та же
    # модель, что пишет ситуацию.
    checker_model: str | None = None
    checker_reasoning_effort: ReasoningEffort | None = None
    timeout_seconds: float = 120.0
    # Локальная модель отвечает на запросы по очереди; облачному провайдеру можно больше.
    concurrency: int = Field(default=1, ge=1)


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
    # Балл, начиная с которого задание считается решённым. При весах по умолчанию 0.7
    # недостижим, если упала половина тестов: 0.6·0.5 + 0.4 = 0.7 только при идеальных
    # рубрике, качестве и скорости. Все тесты без замечаний судьи дают 0.6 и тоже не проходят.
    solved_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    judge_samples: int = Field(default=3, ge=1)
    # Ненулевая температура нужна, чтобы сэмплы судьи различались и разброс что-то значил.
    judge_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    analyzer_timeout_seconds: float = Field(default=30.0, gt=0.0)


class GenerationSettings(BaseModel):
    # При старте сервиса подхватить незавершённые сессии и запустить пополнение пула.
    autostart: bool = True
    # Пул: столько стандартных экземпляров, проверенных эталоном и без персонализации, сервис
    # держит на каждый текущий шаблон. Из пула выдаётся первое задание сессии, поэтому ответ
    # на создание сессии не ждёт генерации. 0 выключает пул.
    pool_size: int = Field(default=2, ge=0)
    # Экземпляр старше этого не выдаётся и удаляется при пополнении: даже готовый экземпляр
    # не должен месяцами ходить по кандидатам.
    pool_max_age_hours: float = Field(default=24.0, gt=0)
    # Как часто пополнять пул без внешнего повода; ещё пул пополняется после импорта шаблонов.
    pool_refill_interval_seconds: float = Field(default=900.0, gt=0)
    # Сколько раз заменить шаблон задания, который не дал экземпляра, другим того же грейда.
    # Каждая замена — ещё одна генерация, а на слабой машине это минуты.
    replacements_per_task: int = Field(default=3, ge=0)
    # Доля персональных заданий, чья легенда тематически опирается на навык профиля, которого
    # нет в шаблоне. Выбор воспроизводим по seed задания; окружение задачи не меняется.
    adjacent_topic_probability: float = Field(default=0.3, ge=0.0, le=1.0)
    # Сколько seed перебрать, пока эталон не пройдёт все кейсы экземпляра.
    instance_attempts: int = Field(default=5, ge=1)
    # Сколько раз попросить LLM переписать условие с утечкой или без имени функции.
    legend_attempts: int = Field(default=3, ge=1)
    # Легенда — творческий текст: при нулевой температуре экземпляры звучат одинаково.
    legend_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    public_examples: int = Field(default=2, ge=0)
    # Чем проверять ситуацию эссе на факты и пересказ запрещённых фраз: llm, embeddings или
    # both (generation/essay.py). Без эмбеддера режимы с эмбеддингами проверяет LLM.
    essay_check_method: EssayCheckMethod = "llm"
    # Пороги косинусного сходства с предложением ситуации для bge-m3; другой модели эмбеддингов
    # нужны свои пороги.
    essay_fact_threshold: float = Field(default=0.6, ge=-1.0, le=1.0)
    essay_forbidden_threshold: float = Field(default=0.6, ge=-1.0, le=1.0)
    # Полуширина серой зоны вокруг порогов в режиме both: такие факты и фразы решает LLM.
    essay_grey_zone: float = Field(default=0.05, ge=0.0, le=1.0)


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
    generation: GenerationSettings = GenerationSettings()
    sandbox: SandboxSettings = SandboxSettings()


@lru_cache
def get_settings() -> Settings:
    return Settings()
