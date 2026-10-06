"""Структурированный ответ LLM: json_schema, а при отказе — JSON из обычного текста.

Не все OpenAI-совместимые провайдеры поддерживают response_format с json_schema целиком:
одни отвечают ошибкой 4xx, другие игнорируют схему. Поэтому вызов идёт в два шага: со схемой,
а при отказе или ответе не по схеме — без неё, со схемой в тексте запроса и разбором первого
JSON-объекта из ответа. Результат в обоих случаях проверяет pydantic-модель.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from taskgen.contracts import ChatMessage, LLMClient

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)
_DECODER = json.JSONDecoder()


class StructuredOutputError(Exception):
    """Ответ модели не удалось привести к схеме ни со схемой, ни без неё."""


def _first_object(text: str) -> dict[str, Any] | None:
    # raw_decode читает ровно один JSON-объект и сам учитывает скобки внутри строк.
    for match in re.finditer(r"\{", text):
        try:
            value, _ = _DECODER.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def extract_json_object(text: str) -> dict[str, Any]:
    """Первый JSON-объект из ответа: сначала из блока ```json, затем из текста.

    Рассуждения в <think> пропускаются: в них модель часто пишет черновые объекты.
    """
    text = _THINK.sub("", text)
    for block in _FENCE.findall(text):
        if (value := _first_object(block)) is not None:
            return value
    if (value := _first_object(text)) is not None:
        return value
    raise ValueError("В ответе модели нет JSON-объекта")


def _parse[ModelT: BaseModel](text: str, model_cls: type[ModelT]) -> ModelT | None:
    try:
        return model_cls.model_validate(extract_json_object(text))
    except (ValueError, ValidationError):
        return None


def _fallback_instruction(schema: dict[str, Any]) -> ChatMessage:
    return ChatMessage(
        role="user",
        content=(
            "Верни только один JSON-объект без пояснений, строго по этой JSON-схеме:\n"
            + json.dumps(schema, ensure_ascii=False)
        ),
    )


async def complete_structured[ModelT: BaseModel](
    llm: LLMClient,
    messages: list[ChatMessage],
    model_cls: type[ModelT],
    schema: dict[str, Any],
    *,
    model: str | None,
    temperature: float = 0.0,
) -> ModelT:
    """Ответ модели как экземпляр model_cls или StructuredOutputError.

    Ошибка провайдера 5xx и сетевые ошибки не скрываются: повтор без схемы их не исправит.
    """
    try:
        reply = await llm.complete(
            messages, model=model, temperature=temperature, json_schema=schema
        )
    except httpx.HTTPStatusError as error:
        if not error.response.is_client_error:
            raise
    else:
        if (parsed := _parse(reply, model_cls)) is not None:
            return parsed
    reply = await llm.complete(
        [*messages, _fallback_instruction(schema)], model=model, temperature=temperature
    )
    if (parsed := _parse(reply, model_cls)) is not None:
        return parsed
    raise StructuredOutputError(f"Ответ модели не соответствует схеме: {reply[:200]!r}")
