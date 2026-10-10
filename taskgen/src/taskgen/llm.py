"""Клиент OpenAI-совместимого API (/v1/chat/completions и /v1/embeddings) и маршрут по ролям.

Такой протокол отдают Ollama, vLLM и облачные API, поэтому провайдер меняется настройками
llm.providers, а модель роли — настройками самой роли, без кода. Каждый вызов называет модель
строкой провайдер/модель, и ProviderRouter отправляет его провайдеру из префикса.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection, Mapping
from typing import Any

import httpx

from taskgen.contracts import (
    ChatMessage,
    Embedder,
    LLMClient,
    ReasoningEffort,
    StructuredOutput,
)
from taskgen.llm_config import LLMSettingsBase, ProviderSettings, split_model

logger = logging.getLogger(__name__)


class MissingAPIKeyError(httpx.HTTPError):
    """Провайдеру нужен ключ, а он не задан. Запрос в сеть не уходит.

    Наследник httpx.HTTPError: генерация и оценка считают его недоступностью модели, как
    любую сетевую ошибку.
    """


class LimitedLLM:
    """LLMClient, который пропускает к inner не больше limit вызовов одновременно.

    Локальный сервер вроде Ollama обслуживает запросы по очереди, а таймаут клиента считается
    с момента отправки. Без ограничения последние запросы очереди падают по таймауту.
    """

    def __init__(self, inner: LLMClient, *, limit: int) -> None:
        self.inner = inner
        self._semaphore = asyncio.Semaphore(limit)

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        json_schema: dict[str, Any] | None = None,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> str:
        async with self._semaphore:
            return await self.inner.complete(
                messages,
                model=model,
                temperature=temperature,
                json_schema=json_schema,
                reasoning_effort=reasoning_effort,
            )


class ProviderRouter:
    """LLMClient и Embedder над провайдерами ролей.

    Модель вызова — строка провайдер/модель: вызов уходит провайдеру из префикса, а сам
    провайдер получает имя модели без префикса. Без модели вызов идёт модели
    settings.default_model(), а если её нет, это ошибка. У каждого провайдера свой лимит
    одновременных вызовов: облако и локальная модель — разные ресурсы. Эмбеддинги идут
    провайдеру роли embedding без лимита, как и до разделения провайдеров.
    """

    def __init__(
        self, settings: LLMSettingsBase, clients: Mapping[str, Any], *, owned: bool = False
    ) -> None:
        self._settings = settings
        self._clients = dict(clients)
        self._limited = {
            name: LimitedLLM(client, limit=settings.providers[name].concurrency)
            for name, client in self._clients.items()
        }
        self._owned = owned
        # Векторы в БД помечены именем модели без провайдера: см. описание llm.embedding.
        self.model_name = settings.roles()["embedding"].provider_model

    @classmethod
    def from_settings(
        cls, settings: LLMSettingsBase, *, roles: Collection[str] | None = None
    ) -> ProviderRouter:
        """Свой клиент на каждого провайдера, которого называет хотя бы одна из ролей roles.

        roles=None — все роли. Импорту шаблонов нужна только роль embedding, и ему незачем
        предупреждать о ключе облачного судьи.
        """
        chosen = {
            name: role for name, role in settings.roles().items() if roles is None or name in roles
        }
        used = sorted({role.provider for role in chosen.values()})
        for name, role in chosen.items():
            logger.info("Роль %s: модель %s", name, role.model)
        for name in used:
            provider = settings.providers[name]
            if provider.requires_key and provider.api_key is None:
                logger.warning(
                    "У провайдера %s не задан ключ: вызовы его ролей будут завершаться ошибкой. "
                    "Задайте %s или переназначьте роли на другого провайдера",
                    name,
                    settings.key_variable(name),
                )
        clients = {
            name: OpenAICompatibleClient(
                settings.providers[name], name=name, key_variable=settings.key_variable(name)
            )
            for name in used
        }
        return cls(settings, clients, owned=True)

    def providers(self) -> set[str]:
        return set(self._clients)

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        json_schema: dict[str, Any] | None = None,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> str:
        provider, name = self._route(self._model(model))
        return await self._limited[provider].complete(
            messages,
            model=name,
            temperature=temperature,
            json_schema=json_schema,
            reasoning_effort=reasoning_effort,
        )

    def structured_output(self, model: str | None) -> StructuredOutput:
        """Режим ответа по схеме у провайдера модели; его читает evaluation.jsonout."""
        provider, _ = self._route(self._model(model))
        return self._settings.providers[provider].structured_output

    def _model(self, model: str | None) -> str:
        chosen = model or self._settings.default_model()
        if chosen is None:
            raise ValueError("вызов LLM без модели, а модели по умолчанию в настройках нет")
        return chosen

    def _route(self, model: str) -> tuple[str, str]:
        provider, name = split_model(model)
        if provider not in self._limited:
            raise ValueError(
                f"модель {model}: провайдер {provider} не настроен, есть только "
                f"{', '.join(sorted(self._limited))}"
            )
        return provider, name

    async def embed(self, texts: list[str]) -> list[list[float]]:
        provider, name = self._route(self._settings.roles()["embedding"].model)
        return await self._clients[provider].embed(texts, model=name)

    async def aclose(self) -> None:
        if self._owned:
            for client in self._clients.values():
                await client.aclose()


def limited_llm(state: Any) -> ProviderRouter:
    """Клиент LLM с лимитами провайдеров, общий для генерации и оценки.

    Свой клиент приложения уже им является. Подменённый клиент из state.llm обслуживает все
    роли, а лимиты по-прежнему действуют на каждого провайдера отдельно. Новый клиент в
    state.llm получает новый маршрут.
    """
    if isinstance(state.llm, ProviderRouter):
        return state.llm
    router = getattr(state, "limited_llm", None)
    if router is None or getattr(state, "limited_llm_inner", None) is not state.llm:
        settings: LLMSettingsBase = state.settings.llm
        router = ProviderRouter(settings, dict.fromkeys(settings.providers, state.llm))
        state.limited_llm = router
        state.limited_llm_inner = state.llm
    return router


def state_embedder(state: Any) -> Embedder | None:
    """Эмбеддер из app.state.embedder, а без него — LLM-клиент, как в API банка.

    Клиент без embed и model_name не эмбеддер: тогда None, и каждый потребитель сам решает,
    чем обойтись без эмбеддингов.
    """
    embedder = getattr(state, "embedder", None) or state.llm
    if not (hasattr(embedder, "embed") and hasattr(embedder, "model_name")):
        return None
    return embedder


class OpenAICompatibleClient:
    """Клиент одного провайдера; модель называет каждый вызов.

    key_variable — переменная окружения ключа: её называет ошибка о ключе.
    """

    def __init__(
        self,
        provider: ProviderSettings,
        *,
        name: str,
        key_variable: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._provider = provider
        self._name = name
        self._key_variable = key_variable
        headers = {}
        if provider.api_key is not None:
            headers["Authorization"] = f"Bearer {provider.api_key.get_secret_value()}"
        self._http = httpx.AsyncClient(
            base_url=provider.base_url.rstrip("/"),
            headers=headers,
            timeout=provider.timeout_seconds,
            transport=transport,
        )

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        json_schema: dict[str, Any] | None = None,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> str:
        if model is None:
            raise ValueError(f"вызов провайдера {self._name} без модели")
        body: dict[str, Any] = {
            "model": model,
            "messages": [m.model_dump() for m in messages],
            "temperature": temperature,
        }
        mode = self._provider.structured_output
        if json_schema is not None and mode == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "answer", "schema": json_schema, "strict": True},
            }
        elif json_schema is not None and mode == "json_object":
            body["response_format"] = {"type": "json_object"}
        # Ollama через этот API не понимает think: false, а reasoning_effort понимает.
        if reasoning_effort is not None:
            body["reasoning_effort"] = reasoning_effort
        data = await self._post("/chat/completions", body)
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            # Обрыв по длине выглядит дальше как ответ не по схеме; причина видна только здесь.
            logger.warning(
                "Ответ модели %s оборван по длине, токены %s; у Ollama это окно контекста "
                "сервера, docs/taskgen/deployment.md",
                model,
                data.get("usage"),
            )
        # Модель с рассуждениями может истратить весь ответ на них и вернуть content: null.
        return choice["message"].get("content") or ""

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        data = await self._post("/embeddings", {"model": model, "input": texts})
        return [item["embedding"] for item in sorted(data["data"], key=lambda i: i["index"])]

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        variable = self._key_variable
        if self._provider.requires_key and self._provider.api_key is None:
            message = f"У провайдера {self._name} не задан ключ: задайте {variable}"
            logger.error(message)
            raise MissingAPIKeyError(message)
        response = await self._http.post(path, json=body)
        if response.status_code in (401, 403):
            logger.error(
                "Провайдер %s отклонил ключ, HTTP %s; ключ задаётся %s",
                self._name,
                response.status_code,
                variable,
            )
        response.raise_for_status()
        return response.json()

    async def aclose(self) -> None:
        await self._http.aclose()
