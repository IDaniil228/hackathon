"""Ограничение числа одновременных вызовов LLM.

Оценка сессии запускает судью по всем заданиям и сэмплам сразу. Локальный сервер вроде
Ollama обслуживает запросы по очереди, а таймаут клиента считается с момента отправки,
поэтому без ограничения последние запросы очереди падают по таймауту.
"""

from __future__ import annotations

import asyncio
from typing import Any

from taskgen.contracts import ChatMessage, LLMClient


class LimitedLLM:
    """LLMClient, который пропускает к inner не больше limit вызовов одновременно."""

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
    ) -> str:
        async with self._semaphore:
            return await self.inner.complete(
                messages, model=model, temperature=temperature, json_schema=json_schema
            )
