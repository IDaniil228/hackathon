"""Подделки внешних зависимостей модуля оценки."""

from __future__ import annotations

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


class FakeSandbox:
    """SandboxRunner, который отдаёт заранее заданный отчёт и запоминает запросы."""

    def __init__(self, report: TestReport) -> None:
        self.report = report
        self.requests: list[TestRunRequest] = []

    async def run(self, request: Any) -> Any:
        raise NotImplementedError

    async def run_tests(self, request: TestRunRequest) -> TestReport:
        self.requests.append(request)
        return self.report
