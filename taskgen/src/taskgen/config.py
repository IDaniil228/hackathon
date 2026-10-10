"""Настройки сервиса.

Это единственное место, где заданы значения по умолчанию и смысл настроек: документация
(scripts/gen_docs.py) берёт их отсюда, поэтому у каждого поля есть description.
Любое поле переопределяется переменной окружения с префиксом TASKGEN_, вложенное поле —
через двойное подчёркивание, например TASKGEN_SCORING__PASS_THRESHOLD=0.7.
Провайдеры, механика ролей и доказательство вердикта общие с matcher и описаны в llm_config.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from taskgen.contracts import EssayCheckMethod, TaskKind
from taskgen.llm_config import (
    CLOUD_MODEL,
    ENV_DELIMITER,
    LOCAL_MODEL,
    ChatRole,
    EvidenceSettings,
    LLMSettingsBase,
    ModelRole,
    Section,
)

ENV_PREFIX = "TASKGEN_"


class LLMSettings(LLMSettingsBase):
    """Провайдеры моделей и роли, которые их используют.

    Роль называет модель строкой провайдер/модель, и вызов уходит провайдеру из префикса.
    """

    env_prefix = ENV_PREFIX

    generator: ChatRole = Field(
        default=ChatRole(model=LOCAL_MODEL),
        description="Пишет легенды, ситуации эссе, кейсы задачек и текст machine summary",
    )
    checker: ChatRole = Field(
        default=ChatRole(model=CLOUD_MODEL),
        description="Проверяльщик ситуации эссе и кейса задачки: ищет в тексте факты шаблона "
        "и цитирует их. Он же сверяет каждое предложение machine summary с фактами сессий",
    )
    solver: ChatRole = Field(
        default=ChatRole(model=LOCAL_MODEL),
        description="Слепой решатель: решает экземпляр по тому, что увидит кандидат, до "
        "выдачи. Работает при generation.solver_enabled. Слабая модель решателя чаще "
        "отклоняет годные экземпляры",
    )
    judge: ChatRole = Field(
        default=ChatRole(model=CLOUD_MODEL),
        description="Судья по рубрике. Судья другой модели снижает завышение оценок "
        "собственного текста генератора",
    )
    detector: ChatRole = Field(
        default=ChatRole(model=CLOUD_MODEL),
        description="Детектор ИИ; работает при ai_detector_enabled",
    )
    embedding: ModelRole = Field(
        default=ModelRole(model="ollama/bge-m3"),
        description="Модель эмбеддингов описаний шаблонов, навыков кандидата и ситуаций эссе. "
        "Векторы в БД помечены именем модели без провайдера: векторы задаёт модель, и смена "
        "провайдера той же модели не требует пересчёта",
    )

    def default_model(self) -> str | None:
        """Вызов без модели идёт генератору."""
        return self.generator.model


class SelectionSettings(Section):
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


class ScoringSettings(Section):
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
        "умолчанию 0.7 недостижим, если упала половина взвешенных тестов: качество и "
        "скорость идут с множителем доли тестов, и 0.6·0.5 + 0.25 + 0.5·0.15 = 0.625 даже "
        "при идеальных рубрике, качестве и скорости. Все тесты без балла рубрики, качества "
        "и скорости дают 0.6 и тоже не проходят",
    )
    required_case_groups: list[str] = Field(
        default=[],
        description="Группы кейсов, обязательные по умолчанию: задание с кодом решено, "
        "только если пройдены все кейсы каждой такой группы, какой бы ни была оценка. "
        "Действует на группы, у которых шаблон не задал required; шаблон включает и "
        "выключает гейт своей группы сам. Пустой список — гейт только там, где его включил "
        "шаблон",
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
        "честные цитаты от самоутверждений кандидата. Числа замера — docs/taskgen/security.md",
    )
    summary_attempts: int = Field(
        default=2,
        ge=1,
        description="Сколько раз модель пишет machine summary. Текст, который отклонил барьер "
        "или проверяльщик, уходит писателю на переписывание вместе с причинами; после "
        "последней попытки рекрутер видит текст по шаблону. Каждая попытка — вызов писателя "
        "и, если текст прошёл барьер, вызов проверяльщика. 1 — без переписывания",
    )
    analyzer_timeout_seconds: float = Field(
        default=30.0,
        gt=0.0,
        description="Бюджет времени на все статические анализаторы одного ответа, секунды. "
        "Анализаторы работают в песочнице одним запуском; инструмент, которому не хватило "
        "бюджета, отдаёт пустой отчёт",
    )


class GenerationSettings(Section):
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
    adjacent_topic_failure: Literal["error", "drop"] = Field(
        default="error",
        description="Что делать, если LLM-проверяльщик не нашёл тему легенды в сюжете за "
        "legend_attempts попыток. error — обычная ошибка генерации экземпляра: дальше "
        "работает замена шаблона. drop — экземпляр генерируется заново без темы, а в "
        "tasks.personalization тема записывается в adjacent_skill_dropped",
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
        "проверку, и по умолчанию она выключена: бенчмарк scripts/essay_check_bench.py "
        "quotes с bge-m3 не нашёл порога, который не отсекает предложения с фактом и "
        "отсекает предложения о другом. Числа замера — docs/taskgen/security.md",
    )
    essay_grey_zone: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Полуширина серой зоны вокруг порогов в режиме both: такие факты и фразы "
        "решает LLM",
    )


class SandboxSettings(Section):
    """Песочница Piston, где исполняются генераторы тестов, эталоны и решения."""

    piston_url: str = Field(default="http://localhost:2000", description="Адрес Piston API")
    run_timeout_ms: int = Field(
        default=3000,
        description="Лимит времени одного запуска, миллисекунды; окружение может поднять его "
        "до своего минимума",
    )
    compile_timeout_ms: int = Field(
        default=30000,
        ge=1,
        description="Лимит времени компиляции одного запуска, миллисекунды, по стенным часам "
        "и по процессорному времени. Без него Piston даёт компиляции 10 с, и на загруженной "
        "машине g++ не укладывался в них даже на эталонном решении. Потолок Piston "
        "PISTON_COMPILE_TIMEOUT и PISTON_COMPILE_CPU_TIME должен быть не меньше",
    )
    run_memory_limit_bytes: int = Field(
        default=256 * 1024 * 1024,
        description="Лимит памяти одного запуска, байты; окружение может поднять его до "
        "своего минимума",
    )
    stdio_concurrency: int = Field(
        default=4,
        ge=1,
        description="Сколько кейсов задачи stdio исполняется одновременно: программа "
        "кандидата запускается на каждом кейсе отдельно. Меньшее значение снижает нагрузку на "
        "Piston, большее ускоряет прогон",
    )


class ImportCheckSettings(Section):
    """Проверка шаблона с кодом в песочнице при импорте.

    Лимиты времени и памяти одного запуска — те же sandbox.run_timeout_ms и
    sandbox.run_memory_limit_bytes, что у генерации: решение, которое укладывается в лимит
    только при импорте, провалило бы генерацию экземпляра.
    """

    seeds: int = Field(
        default=3,
        ge=1,
        description="Сколько seed проверяется: 0, 1, … seeds − 1. На каждом seed каждое верное "
        "решение проходит все кейсы, а каждое неверное не решено",
    )
    concurrency: int = Field(
        default=4,
        ge=1,
        description="Сколько шаблонов проверяется одновременно; запуски одного шаблона идут по "
        "очереди. Меньшее значение снижает нагрузку на Piston: под большой параллельной "
        "нагрузкой он возвращал для C++ результаты без кейсов, и шаблон отклонялся бы ложно",
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
    evidence: EvidenceSettings = Field(
        default=EvidenceSettings(), description=EvidenceSettings.__doc__
    )
    generation: GenerationSettings = Field(
        default=GenerationSettings(), description=GenerationSettings.__doc__
    )
    sandbox: SandboxSettings = Field(default=SandboxSettings(), description=SandboxSettings.__doc__)
    import_check: ImportCheckSettings = Field(
        default=ImportCheckSettings(), description=ImportCheckSettings.__doc__
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
