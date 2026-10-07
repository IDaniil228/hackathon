"""Подделки внешних зависимостей модуля оценки."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import httpx

from taskgen.contracts import ChatMessage, TestReport, TestRunRequest

Responder = Callable[[list[ChatMessage], dict[str, Any] | None], str]


class FakeLLM:
    """LLMClient, который отвечает функцией responder и запоминает каждый вызов."""

    def __init__(self, responder: Responder | list[str]) -> None:
        if isinstance(responder, list):
            replies = iter(responder)
            self._responder: Responder = lambda _messages, _schema: next(replies)
        else:
            self._responder = responder
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        json_schema: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "temperature": temperature,
                "json_schema": json_schema,
                "reasoning_effort": reasoning_effort,
            }
        )
        return self._responder(messages, json_schema)

    async def aclose(self) -> None:
        """Приложение закрывает клиента LLM при остановке."""


def http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://llm/chat/completions")
    response = httpx.Response(status, request=request, text="response_format не поддержан")
    return httpx.HTTPStatusError("ошибка провайдера", request=request, response=response)


def unit(angle_cos: float, axis: int = 0, dims: int = 6) -> list[float]:
    """Вектор с косинусом angle_cos к оси axis; остаток уходит в последнее измерение."""
    vector = [0.0] * dims
    vector[axis] = angle_cos
    vector[-1] = math.sqrt(max(0.0, 1.0 - angle_cos**2))
    return vector


class TableEmbedder:
    """Эмбеддер по таблице; текст вне таблицы — ошибка теста, а не случайный вектор."""

    def __init__(self, vectors: dict[str, list[float]], *, model: str = "table") -> None:
        self.model_name = model
        self.vectors = vectors
        self.calls: list[list[str]] = []
        self.fail: Exception | None = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        if self.fail is not None:
            raise self.fail
        return [self.vectors[text] for text in texts]

    def embedded(self) -> list[str]:
        return [text for call in self.calls for text in call]


class FakeSandbox:
    """SandboxRunner, который отдаёт заранее заданный отчёт и запоминает запросы.

    Запуск анализа исполняет настоящий драйвер анализаторов локально, как LocalSandbox.
    """

    def __init__(self, report: TestReport) -> None:
        self.report = report
        self.requests: list[TestRunRequest] = []

    async def run(self, request: Any) -> Any:
        from tests.generation.fakes import LocalSandbox

        assert request.runtime == "analyzers", request.runtime
        return await LocalSandbox().run(request)

    async def run_tests(self, request: TestRunRequest) -> TestReport:
        self.requests.append(request)
        return self.report
