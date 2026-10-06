"""Клиент OpenAI-совместимого API: /v1/chat/completions и /v1/embeddings.

Такой протокол отдают Ollama, vLLM и шлюзы к облачным моделям, поэтому провайдер меняется
настройками base_url, api_key и именем модели, без кода.
"""

from __future__ import annotations

from typing import Any

import httpx

from taskgen.config import LLMSettings
from taskgen.contracts import ChatMessage


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
        response = await self._http.post("/chat/completions", json=body)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        response = await self._http.post(
            "/embeddings", json={"model": self._settings.embedding_model, "input": texts}
        )
        response.raise_for_status()
        data = sorted(response.json()["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in data]

    async def aclose(self) -> None:
        await self._http.aclose()
