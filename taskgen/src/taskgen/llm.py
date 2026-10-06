"""Клиент OpenAI-совместимого API: /v1/chat/completions и /v1/embeddings.

Такой протокол отдают Ollama, vLLM и шлюзы к облачным моделям, поэтому провайдер меняется
настройками base_url, api_key и именем модели, без кода.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from taskgen.config import LLMSettings, Settings
from taskgen.contracts import ChatMessage, Embedder, LLMClient, ReasoningEffort


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


def limited_llm(state: Any) -> LimitedLLM:
    """Ограничитель из app.state.limited_llm, общий для генерации и оценки.

    Оба модуля ходят в одну модель, поэтому лимит одновременных запросов к ней один. Новый
    клиент в state.llm получает новый ограничитель.
    """
    llm = getattr(state, "limited_llm", None)
    if llm is None or llm.inner is not state.llm:
        settings: Settings = state.settings
        llm = LimitedLLM(state.llm, limit=settings.llm.concurrency)
        state.limited_llm = llm
    return llm


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
    def __init__(self, settings: LLMSettings, http: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._http = http or httpx.AsyncClient(
            base_url=settings.base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.api_key}"},
            timeout=settings.timeout_seconds,
        )
        self.model_name = settings.embedding_model

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        json_schema: dict[str, Any] | None = None,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> str:
        body: dict[str, Any] = {
            "model": model or self._settings.generator_model,
            "messages": [m.model_dump() for m in messages],
            "temperature": temperature,
        }
        if json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "answer", "schema": json_schema, "strict": True},
            }
        # Ollama через этот API не понимает think: false, а reasoning_effort понимает.
        if reasoning_effort is not None:
            body["reasoning_effort"] = reasoning_effort
        response = await self._http.post("/chat/completions", json=body)
        response.raise_for_status()
        # Модель с рассуждениями может истратить весь ответ на них и вернуть content: null.
        return response.json()["choices"][0]["message"].get("content") or ""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        response = await self._http.post(
            "/embeddings", json={"model": self._settings.embedding_model, "input": texts}
        )
        response.raise_for_status()
        data = sorted(response.json()["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in data]

    async def aclose(self) -> None:
        await self._http.aclose()
