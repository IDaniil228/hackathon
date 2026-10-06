"""Настройки сервиса.

Это единственное место, где заданы значения по умолчанию и смысл настроек: документация
(scripts/gen_docs.py) берёт их отсюда, поэтому у каждого поля есть description.
Любое поле переопределяется переменной окружения с префиксом TASKGEN_, вложенное поле —
через двойное подчёркивание, например TASKGEN_SCORING__PASS_THRESHOLD=0.7.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from taskgen.contracts import EssayCheckMethod, ReasoningEffort, TaskKind

_REASONING = (
    "Поле reasoning_effort запроса к модели {role}; null — поле не передаётся, и модель "
    'рассуждает как обычно. Значение зависит от модели: на слабой машине "none" сокращает '
    "ответ в разы, рассуждение qwen3.5:4b на простой запрос занимало тысячи токенов и иногда "
    "не оставляло места самому ответу"
)


class LLMSettings(BaseModel):
    """OpenAI-совместимый провайдер: Ollama, vLLM или облачный шлюз."""

    base_url: str = Field(
        default="http://localhost:11434/v1",
        description="Адрес OpenAI-совместимого API: /chat/completions и /embeddings",
    )
    api_key: str = Field(
        default="ollama",
        description="Ключ провайдера, уходит в заголовке Authorization: Bearer; "
        "Ollama его не проверяет",
    )
    generator_model: str = Field(
        default="qwen3.5:9b",
        description="Модель, которая пишет легенды, ситуации эссе и текст machine summary",
    )
    judge_model: str = Field(
        default="qwen3.5:4b",
        description="Модель судьи по рубрике и детектора ИИ. Судья другой модели снижает "
        "завышение оценок собственного текста генератора",
    )
    embedding_model: str = Field(
        default="bge-m3", description="Модель эмбеддингов описаний шаблонов и навыков кандидата"
    )
    generator_reasoning_effort: ReasoningEffort | None = Field(
        default=None, description=_REASONING.format(role="генерации")
    )
    judge_reasoning_effort: ReasoningEffort | None = Field(
        default=None, description=_REASONING.format(role="судьи и детектора ИИ")
    )
    checker_model: str | None = Field(
        default=None,
        description="Проверяльщик ситуации эссе: ищет в ней факты шаблона и цитирует их; "
        "null — та же модель, что пишет ситуацию",
    )
    checker_reasoning_effort: ReasoningEffort | None = Field(
        default=None, description=_REASONING.format(role="проверяльщика ситуации эссе")
    )
    solver_model: str | None = Field(
        default=None,
        description="Слепой решатель: решает экземпляр по тому, что увидит кандидат, до "
        "выдачи. null — модель проверяльщика, а без неё — модель генерации. Слабая модель "
        "решателя чаще отклоняет годные экземпляры",
    )
    solver_reasoning_effort: ReasoningEffort | None = Field(
        default=None, description=_REASONING.format(role="слепого решателя")
    )
    timeout_seconds: float = Field(
        default=120.0, description="Таймаут одного HTTP-запроса к провайдеру, секунды"
    )
    concurrency: int = Field(
        default=1,
        ge=1,
        description="Сколько запросов к модели идут одновременно, общий лимит генерации и "
        "оценки. Локальная модель отвечает по очереди; облачному провайдеру можно больше",
    )


class SelectionSettings(BaseModel):
    """Отбор шаблонов: p ∝ exp(−d / T), d = alpha·|Δгрейд| + beta·(1 − сходство).

    |Δгрейд| — разность позиций грейдов в списке, упорядоченном по level, а не разность level.
    Значения по умолчанию; тест на категорию переопределяет их строкой test_configs.
    Шаблоны каждого типа отбираются отдельно, и первые min_claimed_per_kind заданий типа
    берутся только из шаблонов заявленного грейда.
    """

    temperature: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Температура отбора t; шкала 0..1 переводится в T = t / (1 − t): "
        "0 — только ближайшие шаблоны, 1 — равномерно",
    )
    alpha: float = Field(default=1.0, ge=0.0, description="Вес разницы грейдов в расстоянии d")
    beta: float = Field(default=1.0, ge=0.0, description="Вес несходства навыков в расстоянии d")
    code_tasks: int = Field(
        default=3,
        ge=0,
        description="Число заданий с кодом в тесте. Вместе с эссе — пять заданий: на слабой "
        "машине каждое из них — минуты генерации и десятки минут работы кандидата",
    )
    essay_tasks: int = Field(default=2, ge=0, description="Число эссе в тесте")
    quiz_tasks: int = Field(
        default=5,
        ge=0,
        description="Число тестовых задачек с выбором ответа. Задачка занимает у кандидата "
        "пару минут, поэтому их больше, чем остальных заданий",
    )
    min_claimed_per_kind: int = Field(
        default=1,
        ge=0,
        description="Квота: сколько первых заданий каждого типа выбираются только среди "
        "шаблонов заявленного грейда. Без квоты единственное задание типа при t = 0.3 и трёх "
        "шаблонах на грейд оказывается чужого грейда в 16 % тестов. Если шаблонов "
        "заявленного грейда меньше квоты, тест не собирается",
    )

    def tasks_of(self, kind: TaskKind) -> int:
        """Число заданий типа kind в тесте."""
        return {
            TaskKind.CODE: self.code_tasks,
            TaskKind.ESSAY: self.essay_tasks,
            TaskKind.QUIZ: self.quiz_tasks,
        }[kind]

    @model_validator(mode="after")
    def _some_tasks(self) -> SelectionSettings:
        if not any(self.tasks_of(kind) for kind in TaskKind):
            raise ValueError("в тесте должно быть хотя бы одно задание")
        return self


class ScoringSettings(BaseModel):
    """Балл за задание с кодом — взвешенная сумма компонентов из диапазона 0..1."""

    weight_tests: float = Field(default=0.6, description="Вес доли пройденных скрытых тестов")
    weight_rubric: float = Field(default=0.25, description="Вес балла судьи по рубрике")
    weight_quality: float = Field(
        default=0.1, description="Вес качества кода по метрикам статических анализаторов"
    )
    weight_performance: float = Field(
        default=0.05, description="Вес скорости и памяти относительно эталона"
    )
    weight_quiz: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Вес блока тестовых задачек в общем балле сессии; остальное — средний балл "
        "заданий с кодом и эссе. Пять задачек — минуты работы против часов на практические "
        "задания, а угаданный ответ даёт 1 с вероятностью 1/4: при 0.1 одни задачки дают не "
        "больше 0.1 общего балла, а угадывание в среднем 0.025. В грейд по уровням задачки "
        "не входят",
    )
    pass_threshold: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        description="Доля решённых заданий уровня, начиная с которой уровень подтверждён",
    )
    solved_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Балл, начиная с которого задание считается решённым. При весах по "
        "умолчанию 0.7 недостижим, если упала половина тестов: 0.6·0.5 + 0.4 = 0.7 только "
        "при идеальных рубрике, качестве и скорости. Все тесты без замечаний судьи дают 0.6 "
        "и тоже не проходят",
    )
    min_evidence_per_level: int = Field(
        default=1,
        ge=0,
        description="Сколько заданий с кодом и эссе заявленного уровня нужно для вердикта о "
        "грейде; меньше — исход insufficient вместо случайного вердикта. Тестовые задачки "
        "не считаются",
    )
    judge_samples: int = Field(
        default=3, ge=1, description="Сколько раз судья оценивает ответ; итог — нижняя медиана"
    )
    judge_temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Температура судьи. Ненулевая нужна, чтобы сэмплы различались и разброс "
        "что-то значил",
    )
    judge_quote_threshold: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        description="Порог косинусного сходства цитат судьи с текстом пункта рубрики: ниже "
        "него вердикт пункта становится no. Без эмбеддера проверка пропускается с замечанием. "
        "null выключает проверку, и по умолчанию она выключена: бенчмарк "
        "scripts/essay_check_bench.py quotes с bge-m3 не нашёл порога, который отделяет "
        "честные цитаты от самоутверждений кандидата. При 0.42 честные цитаты не отсекаются, "
        "но проходят все самоутверждения с понятием пункта и половина самоутверждений без "
        "темы; при 0.5 отсекается 7 % честных цитат",
    )
    analyzer_timeout_seconds: float = Field(
        default=30.0, gt=0.0, description="Лимит времени одного статического анализатора, секунды"
    )


class GenerationSettings(BaseModel):
    """Генерация экземпляров заданий и пул готовых экземпляров."""

    autostart: bool = Field(
        default=True,
        description="При старте сервиса подхватить незавершённые сессии и запустить "
        "пополнение пула",
    )
    pool_size: int = Field(
        default=2,
        ge=0,
        description="Сколько стандартных экземпляров, проверенных эталоном и без "
        "персонализации, сервис держит на каждый текущий шаблон. Из пула выдаётся первое "
        "задание сессии, поэтому ответ на создание сессии не ждёт генерации. 0 выключает пул",
    )
    pool_max_age_hours: float = Field(
        default=24.0,
        gt=0,
        description="Экземпляр пула старше этого не выдаётся и удаляется при пополнении: даже "
        "готовый экземпляр не должен месяцами ходить по кандидатам",
    )
    pool_refill_interval_seconds: float = Field(
        default=900.0,
        gt=0,
        description="Как часто пополнять пул без внешнего повода; ещё пул пополняется после "
        "импорта шаблонов",
    )
    replacements_per_task: int = Field(
        default=3,
        ge=0,
        description="Сколько раз заменить шаблон задания, который не дал экземпляра, другим "
        "того же грейда. Каждая замена — ещё одна генерация, а на слабой машине это минуты",
    )
    adjacent_topic_probability: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Доля персональных заданий, чья легенда тематически опирается на навык "
        "профиля, которого нет в шаблоне. Выбор воспроизводим по seed задания; окружение "
        "задачи не меняется",
    )
    instance_attempts: int = Field(
        default=5,
        ge=1,
        description="Сколько seed перебрать, пока эталон не пройдёт все кейсы экземпляра",
    )
    legend_attempts: int = Field(
        default=3,
        ge=1,
        description="Сколько раз попросить LLM переписать условие, которое не прошло проверки",
    )
    legend_temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Температура легенды. Легенда — творческий текст: при нулевой температуре "
        "экземпляры звучат одинаково",
    )
    solver_enabled: bool = Field(
        default=False,
        description="Проверять экземпляр слепым решателем до выдачи: задачку он решает по "
        "кейсу и вариантам, задание с кодом — по условию и примерам, а решение идёт на все "
        "кейсы. Без решателя не проверены полнота условия и отсутствие второго верного ответа "
        "в кейсе. Включать стоит с сильной моделью решателя, например облачной: на слабой "
        "машине это ещё один вызов модели на задание и ложные отказы",
    )
    solver_attempts: int = Field(
        default=1,
        ge=1,
        description="Сколько раз решатель пробует решить один черновик, прежде чем признать "
        "его нерешаемым и отправить на переписывание",
    )
    public_examples: int = Field(
        default=2,
        ge=0,
        description="Сколько первых кейсов задания с кодом показать кандидату примерами; "
        "если кейсов больше одного, хотя бы один остаётся скрытым",
    )
    essay_check_method: EssayCheckMethod = Field(
        default="llm",
        description="Чем проверять ситуацию эссе на факты и пересказ запрещённых фраз: llm, "
        "embeddings или both, где LLM решает только серую зону вокруг порогов. Без эмбеддера "
        "режимы с эмбеддингами проверяет LLM. LLM по умолчанию: на бенчмарке "
        "scripts/essay_check_bench.py эмбеддинги принимают отрицание факта за сам факт и не "
        "отличают запрещённую фразу от темы ситуации",
    )
    essay_fact_threshold: float = Field(
        default=0.6,
        ge=-1.0,
        le=1.0,
        description="Порог косинусного сходства факта с предложением ситуации для bge-m3, "
        "подобран бенчмарком по F1; другой модели эмбеддингов нужен свой порог",
    )
    essay_forbidden_threshold: float = Field(
        default=0.425,
        ge=-1.0,
        le=1.0,
        description="Порог косинусного сходства запрещённой фразы с предложением ситуации "
        "для bge-m3, подобран тем же бенчмарком",
    )
    essay_quote_threshold: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        description="Порог косинусного сходства цитат LLM-проверяльщика с фактом: ниже него "
        "факт не засчитан. Без эмбеддера проверка пропускается с замечанием. null выключает "
        "проверку, и по умолчанию она выключена: на бенчмарке scripts/essay_check_bench.py "
        "quotes с bge-m3 при 0.44 предложения с фактом не отсекаются, но 61 % предложений о "
        "другом проходит, а отрицание факта проходит всегда",
    )
    essay_grey_zone: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Полуширина серой зоны вокруг порогов в режиме both: такие факты и фразы "
        "решает LLM",
    )


class SandboxSettings(BaseModel):
    """Песочница Piston, где исполняются генераторы тестов, эталоны и решения."""

    piston_url: str = Field(default="http://localhost:2000", description="Адрес Piston API")
    run_timeout_ms: int = Field(
        default=3000,
        description="Лимит времени одного запуска, миллисекунды; окружение может поднять его "
        "до своего минимума",
    )
    run_memory_limit_bytes: int = Field(
        default=256 * 1024 * 1024,
        description="Лимит памяти одного запуска, байты; окружение может поднять его до "
        "своего минимума",
    )


class Settings(BaseSettings):
    """Корень настроек; переменные окружения и файл .env читаются при старте."""

    model_config = SettingsConfigDict(
        env_prefix="TASKGEN_", env_nested_delimiter="__", env_file=".env", extra="ignore"
    )

    database_url: str = Field(
        default="postgresql+asyncpg://taskgen:taskgen@localhost:5432/taskgen",
        description="Адрес Postgres с расширением pgvector в формате SQLAlchemy asyncpg",
    )
    bootstrap_admin_key: str | None = Field(
        default=None,
        description="Первый admin-ключ; остальные ключи создаются через API и хранятся хэшами",
    )
    ai_detector_enabled: bool = Field(
        default=False,
        description="Включить детектор ИИ. Его сигнал хранится отдельно и не влияет ни на "
        "балл, ни на summary",
    )

    llm: LLMSettings = Field(default=LLMSettings(), description=LLMSettings.__doc__)
    selection: SelectionSettings = Field(
        default=SelectionSettings(), description=SelectionSettings.__doc__
    )
    scoring: ScoringSettings = Field(default=ScoringSettings(), description=ScoringSettings.__doc__)
    generation: GenerationSettings = Field(
        default=GenerationSettings(), description=GenerationSettings.__doc__
    )
    sandbox: SandboxSettings = Field(default=SandboxSettings(), description=SandboxSettings.__doc__)


@lru_cache
def get_settings() -> Settings:
    return Settings()
