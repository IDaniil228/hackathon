"""Настройки провайдеров моделей, ролей и доказательства вердикта.

Это общая часть настроек LLM: её расширяет taskgen.config.LLMSettings, а сервис matcher
получает модуль копией (matcher/scripts/sync_llm.py) и расширяет своими ролями. Поэтому здесь
нет ни ролей сервиса, ни префикса его переменных окружения: роли — поля наследника, префикс —
его атрибут env_prefix.
"""

from __future__ import annotations

from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from taskgen.contracts import EvidenceMode, ReasoningEffort, StructuredOutput

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


def provider_key_variable(provider: str, env_prefix: str) -> str:
    """Переменная окружения, которая задаёт ключ провайдера у сервиса с префиксом env_prefix.

    Путь до ключа одинаков у всех сервисов: поле llm корня настроек, затем providers.
    """
    return ENV_DELIMITER.join([f"{env_prefix}LLM", "PROVIDERS", provider, "API_KEY"]).upper()


# Провайдеры по умолчанию. Переменная окружения поля провайдера дополняет его, а не заменяет:
# LLMSettingsBase._merge_defaults.
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


class LLMSettingsBase(Section):
    """Провайдеры моделей и механика ролей; сами роли — поля наследника типа ModelRole.

    Наследник задаёт env_prefix: префикс переменных окружения своего сервиса. Роль embedding
    нужна только сервисам, которые вызывают расчёт эмбеддингов
    """

    env_prefix: ClassVar[str]

    providers: dict[str, ProviderSettings] = Field(
        default={
            name: ProviderSettings.model_validate(fields)
            for name, fields in DEFAULT_PROVIDERS.items()
        },
        description="Провайдеры по имени. Поле провайдера из окружения дополняет провайдера по "
        "умолчанию, а новое имя добавляет провайдера, и тогда base_url обязателен",
    )

    @model_validator(mode="before")
    @classmethod
    def _merge_defaults(cls, data: Any) -> Any:
        # pydantic-settings собирает из <ПРЕФИКС>LLM__PROVIDERS__DEEPSEEK__API_KEY словарь только
        # с этим полем и заменяет им провайдера по умолчанию целиком. То же с ролью и
        # <ПРЕФИКС>LLM__<РОЛЬ>__MODEL. Поэтому частичный словарь дополняется умолчанием.
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

    def default_model(self) -> str | None:
        """Модель вызова, который не назвал модель; None — такой вызов ошибочен."""
        return None

    def key_variable(self, provider: str) -> str:
        """Переменная окружения ключа провайдера у этого сервиса."""
        return provider_key_variable(provider, self.env_prefix)

    def with_timeout(self, seconds: float) -> Self:
        """Копия, где у каждого провайдера таймаут seconds: для замеров и smoke-тестов."""
        providers = {
            name: provider.model_copy(update={"timeout_seconds": seconds})
            for name, provider in self.providers.items()
        }
        return self.model_copy(update={"providers": providers})

    @model_validator(mode="after")
    def _roles_reach_their_providers(self) -> Self:
        for name, role in self.roles().items():
            if role.provider not in self.providers:
                raise ValueError(
                    f"модель {role.model} роли {name} ссылается на провайдера {role.provider}, "
                    f"а в llm.providers есть только {', '.join(sorted(self.providers))}"
                )
        return self


class EvidenceSettings(Section):
    """Чем модель подтверждает вердикт: один способ для судьи ответа и для LLM-проверяльщика
    ситуации эссе, кейса задачки, темы легенды и machine summary. Механика —
    taskgen/evaluation/evidence.py."""

    model_config = ConfigDict(frozen=True)

    mode: EvidenceMode = Field(
        default="spans",
        description="Способ доказательства: quotes — дословные цитаты текста, spans — номера "
        "его участков, которые модели показаны с номерами: строк кода или предложений. "
        "Судья так подтверждает вердикты yes и partial, проверяльщик — найденный факт, "
        "запрещённое понятие, тему или предложение machine summary; у summary участок — "
        "строка фактов с путём и значением. spans по умолчанию: судья и проверяльщик по "
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
