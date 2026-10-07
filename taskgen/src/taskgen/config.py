"""Настройки сервиса.

Это единственное место, где заданы значения по умолчанию и смысл настроек: документация
(scripts/gen_docs.py) берёт их отсюда, поэтому у каждого поля есть description.
Любое поле переопределяется переменной окружения с префиксом TASKGEN_, вложенное поле —
через двойное подчёркивание, например TASKGEN_SCORING__PASS_THRESHOLD=0.7.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from taskgen.contracts import (
    EssayCheckMethod,
    EvidenceMode,
    ReasoningEffort,
    StructuredOutput,
    TaskKind,
)

ENV_PREFIX = "TASKGEN_"
ENV_DELIMITER = "__"

LOCAL_MODEL = "ollama/hf.co/ai-sage/GigaChat3.1-10B-A1.8B-GGUF:q4_K_M"
CLOUD_MODEL = "deepseek/deepseek-flash"


class Section(BaseModel):
    """Раздел настроек: неизвестное поле — ошибка при старте, а не молча забытая переменная.

    Корень Settings лишние поля пропускает: файл .env делят с compose и другими программами.
    """

    model_config = ConfigDict(extra="forbid")


class ProviderSettings(Section):
    """OpenAI-совместимый провайдер: Ollama, vLLM, облачный API или шлюз к нему."""

    base_url: str = Field(
        description="Адрес OpenAI-совместимого API: /chat/completions и /embeddings"
    )
    api_key: SecretStr | None = Field(
        default=None,
        description="Ключ провайдера, уходит в заголовке Authorization: Bearer. Задаётся "
        "только переменной окружения и не попадает ни в журнал, ни в документацию; null — "
        "заголовок не передаётся",
    )
    requires_key: bool = Field(
        default=False,
        description="Провайдеру нужен ключ. Без ключа сервис всё равно стартует: он пишет "
        "предупреждение в журнал, а вызов роли на этом провайдере завершается ошибкой с именем "
        "переменной ключа и не уходит в сеть",
    )
    timeout_seconds: float = Field(
        default=120.0, gt=0, description="Таймаут одного HTTP-запроса к провайдеру, секунды"
    )
    concurrency: int = Field(
        default=1,
        ge=1,
        description="Сколько запросов к провайдеру идут одновременно, общий лимит генерации и "
        "оценки на этом провайдере. Локальная модель отвечает по очереди; облачному провайдеру "
        "можно больше",
    )
    structured_output: StructuredOutput = Field(
        default="json_schema",
        description="Как просить у провайдера ответ по схеме. json_schema — response_format со "
        "схемой, а при отказе 4xx или ответе не по схеме — повтор со схемой в тексте запроса. "
        "json_object и text — сразу схема в тексте запроса и одна попытка; json_object "
        'добавляет response_format {"type": "json_object"}, и провайдер отвечает только '
        "валидным JSON. DeepSeek отвечает 400 на json_schema, а json_object принимает",
    )


def provider_key_variable(provider: str) -> str:
    """Переменная окружения, которая задаёт ключ провайдера."""
    return ENV_DELIMITER.join([f"{ENV_PREFIX}LLM", "PROVIDERS", provider, "API_KEY"]).upper()


# Провайдеры по умолчанию. Переменная окружения поля провайдера дополняет его, а не заменяет:
# LLMSettings._merge_defaults.
DEFAULT_PROVIDERS: dict[str, dict[str, Any]] = {
    "ollama": {"base_url": "http://localhost:11434/v1"},
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "requires_key": True,
        "concurrency": 4,
        "structured_output": "json_object",
    },
}


def _merged(default: dict[str, Any], value: Any) -> Any:
    """value поверх default, если value — словарь полей; иначе value как есть."""
    return {**default, **value} if isinstance(value, dict) else value


def split_model(model: str) -> tuple[str, str]:
    """Провайдер и имя модели у него из строки провайдер/модель.

    Провайдер — часть до первого /: в именах моделей Ollama бывают свои /.
    """
    provider, _, name = model.partition("/")
    if not provider or not name:
        raise ValueError(f"модель {model!r} не в форме провайдер/модель, например ollama/bge-m3")
    return provider, name


class ModelRole(Section):
    """Модель роли вместе с провайдером, у которого она запрашивается."""

    model: str = Field(
        description="Модель в форме провайдер/модель: часть до первого / — имя провайдера из "
        "llm.providers, остальное — имя модели у провайдера, и в нём бывают свои /"
    )

    @field_validator("model")
    @classmethod
    def _has_provider(cls, value: str) -> str:
        split_model(value)
        return value

    @property
    def provider(self) -> str:
        return split_model(self.model)[0]

    @property
    def provider_model(self) -> str:
        """Имя модели у провайдера, без префикса."""
        return split_model(self.model)[1]


class ChatRole(ModelRole):
    reasoning_effort: ReasoningEffort | None = Field(
        default="none",
        description="Поле reasoning_effort запроса к модели роли; null — поле не передаётся, и "
        'модель рассуждает как обычно. "none" сокращает ответ в разы: рассуждение qwen3.5:4b '
        "на простой запрос занимало тысячи токенов и иногда не оставляло места самому ответу. "
        'Модель без рассуждений, как GigaChat 3.1 в Ollama, принимает только "none", а на '
        'другое значение отвечает HTTP 400 "does not support thinking"',
    )


class LLMSettings(Section):
    """Провайдеры моделей и роли, которые их используют.

    Роль называет модель строкой провайдер/модель, и вызов уходит провайдеру из префикса.
    """

    providers: dict[str, ProviderSettings] = Field(
        default={
            name: ProviderSettings.model_validate(fields)
            for name, fields in DEFAULT_PROVIDERS.items()
        },
        description="Провайдеры по имени. Поле провайдера из окружения дополняет провайдера по "
        "умолчанию, а новое имя добавляет провайдера, и тогда base_url обязателен",
    )
    generator: ChatRole = Field(
        default=ChatRole(model=LOCAL_MODEL),
        description="Пишет легенды, ситуации эссе, кейсы задачек и текст machine summary",
    )
    checker: ChatRole = Field(
        default=ChatRole(model=CLOUD_MODEL),
        description="Проверяльщик ситуации эссе и кейса задачки: ищет в тексте факты шаблона "
        "и цитирует их",
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

    @model_validator(mode="before")
    @classmethod
    def _merge_defaults(cls, data: Any) -> Any:
        # pydantic-settings собирает из TASKGEN_LLM__PROVIDERS__DEEPSEEK__API_KEY словарь только
        # с этим полем и заменяет им провайдера по умолчанию целиком. То же с ролью и
        # TASKGEN_LLM__GENERATOR__MODEL. Поэтому частичный словарь дополняется умолчанием.
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for name, value in data.items():
            if name == "providers" and isinstance(value, dict):
                data[name] = {
                    provider: _merged(DEFAULT_PROVIDERS.get(provider, {}), fields)
                    for provider, fields in {**DEFAULT_PROVIDERS, **value}.items()
                }
            elif name in cls.model_fields and isinstance(
                default := cls.model_fields[name].default, BaseModel
            ):
                data[name] = _merged(default.model_dump(), value)
        return data

    def roles(self) -> dict[str, ModelRole]:
        """Все роли по имени поля; новая роль — новое поле типа ModelRole."""
        return {
            name: value
            for name in type(self).model_fields
            if isinstance(value := getattr(self, name), ModelRole)
        }

    def chat_roles(self) -> dict[str, ChatRole]:
        return {name: role for name, role in self.roles().items() if isinstance(role, ChatRole)}

    def with_timeout(self, seconds: float) -> LLMSettings:
        """Копия, где у каждого провайдера таймаут seconds: для замеров и smoke-тестов."""
        providers = {
            name: provider.model_copy(update={"timeout_seconds": seconds})
            for name, provider in self.providers.items()
        }
        return self.model_copy(update={"providers": providers})

    @model_validator(mode="after")
    def _roles_reach_their_providers(self) -> LLMSettings:
        for name, role in self.roles().items():
            if role.provider not in self.providers:
                raise ValueError(
                    f"модель {role.model} роли {name} ссылается на провайдера {role.provider}, "
                    f"а в llm.providers есть только {', '.join(sorted(self.providers))}"
                )
        return self


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


class EvidenceSettings(Section):
    """Чем модель подтверждает вердикт: один способ для судьи ответа и для LLM-проверяльщика
    ситуации эссе, кейса задачки и темы легенды. Механика — taskgen/evaluation/evidence.py."""

    model_config = ConfigDict(frozen=True)

    mode: EvidenceMode = Field(
        default="spans",
        description="Способ доказательства: quotes — дословные цитаты текста, spans — номера "
        "его участков, которые модели показаны с номерами: строк кода или предложений. "
        "Судья так подтверждает вердикты yes и partial, проверяльщик — найденный факт, "
        "запрещённое понятие или тему. spans по умолчанию: судья и проверяльщик по "
        "умолчанию — deepseek-flash, и на нём ссылки не уступают цитатам. GigaChat с "
        "цитатами терял больше половины верных вердиктов судьи, но со ссылками засчитывает и "
        "самоутверждения кандидата. Замеры — docs/taskgen/validation.md",
    )
    repairs: int = Field(
        default=2,
        ge=0,
        description="Сколько раз переспросить модель с перечнем ошибок, если её ответ не по "
        "схеме, без вердикта по пункту или ссылается на текст, которого нет: цитату не "
        "дословно или номер участка, которого нет. Каждый повтор — ещё один вызов модели. "
        "0 выключает повторы",
    )
    max_spans: int | None = Field(
        default=None,
        ge=1,
        description="Сколько участков самое большее подтверждают один вердикт в режиме spans; "
        "вердикт с большим числом участков не засчитывается. Ссылка на весь текст не "
        "доказывает ничего и обходит проверку смысла цитат. null — без предела, и по "
        "умолчанию предела нет: на наборе ложных срабатываний судьи честные ссылки доходили "
        "до 8 участков, а атаки до 5, и число не отделяет одни от других",
    )


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
