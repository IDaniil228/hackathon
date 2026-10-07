"""Структурированный ответ LLM: json_schema, а при отказе — JSON из обычного текста.

Не все OpenAI-совместимые провайдеры поддерживают response_format с json_schema целиком:
одни отвечают ошибкой 4xx, другие игнорируют схему. Поэтому вызов идёт в два шага: со схемой,
а при отказе или ответе не по схеме — без неё, со схемой в тексте запроса и разбором первого
JSON-объекта из ответа. Провайдер, о котором известно, что json_schema он не принимает
(llm.providers.<имя>.structured_output), сразу получает второй шаг. Результат в обоих случаях
проверяет pydantic-модель.

Ответ не по схеме или с ошибкой, которую нашёл вызывающий, можно отклонить: запрос
повторяется с перечнем ошибок. Число таких повторов задаёт вызывающий.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from taskgen.contracts import ChatMessage, LLMClient, ReasoningEffort, StructuredOutput

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)
_DECODER = json.JSONDecoder()


class StructuredOutputError(Exception):
    """Ответ модели не удалось привести к схеме ни со схемой, ни без неё."""


class EmptyReplyError(StructuredOutputError):
    """Модель не вернула текста вне рассуждений.

    Повтор без схемы это не исправит: модель снова истратит ответ на рассуждения. Поэтому
    второй вызов не делается, а решение о повторе принимает вызывающий.
    """


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


def _parse[ModelT: BaseModel](text: str, model_cls: type[ModelT]) -> ModelT | str:
    """Экземпляр model_cls или текст ошибки разбора для повтора."""
    try:
        return model_cls.model_validate(extract_json_object(text))
    except ValidationError as error:
        problems = [
            f"{'.'.join(str(part) for part in item['loc']) or 'объект'}: {item['msg']}"
            for item in error.errors()[:5]
        ]
        return "ответ не по схеме — " + "; ".join(problems)
    except ValueError as error:
        return str(error)


def _fallback_instruction(schema: dict[str, Any]) -> ChatMessage:
    return ChatMessage(
        role="user",
        content=(
            "Верни только один JSON-объект без пояснений, строго по этой JSON-схеме:\n"
            + json.dumps(schema, ensure_ascii=False)
        ),
    )


def _repair_instruction(problems: list[str]) -> ChatMessage:
    return ChatMessage(
        role="user",
        content=(
            "Твой предыдущий ответ на этот запрос не принят: "
            + "; ".join(problems)
            + ". Ответь заново целиком, по той же схеме, и исправь это."
        ),
    )


def _structured_output(llm: LLMClient, model: str | None) -> StructuredOutput:
    """Режим провайдера модели; клиент без метода structured_output считается json_schema.

    Метод есть у llm.ProviderRouter. Подделки в тестах и обёртки его не имеют и идут прежним
    путём в два шага.
    """
    query = getattr(llm, "structured_output", None)
    return "json_schema" if query is None else query(model)


async def complete_structured[ModelT: BaseModel](
    llm: LLMClient,
    messages: list[ChatMessage],
    model_cls: type[ModelT],
    schema: dict[str, Any],
    *,
    model: str | None,
    temperature: float = 0.0,
    reasoning_effort: ReasoningEffort | None = None,
    check: Callable[[ModelT], list[str]] | None = None,
    repairs: int = 0,
    repair_log: list[str] | None = None,
) -> ModelT:
    """Ответ модели как экземпляр model_cls или StructuredOutputError.

    check возвращает претензии к разобранному ответу. Если ответ с претензиями или не по
    схеме, запрос повторяется с перечнем ошибок, и так не больше repairs раз. Прошлый ответ
    в повтор не входит: запрос не растёт, а окно контекста Ollama 4096 токенов его не
    вмещало. Причина каждого повтора дописывается в repair_log. Если претензии остались
    после всех повторов или повтор отклонён ошибкой 4xx, функция возвращает последний
    разобранный ответ: что с ним делать, решает вызывающий.

    Ошибка провайдера 5xx и сетевые ошибки не скрываются: повтор без схемы их не исправит.
    Провайдер без json_schema сразу получает схему в тексте запроса, одной попыткой.
    """
    mode = _structured_output(llm, model)
    conversation = messages
    json_schema: dict[str, Any] | None = schema
    reply: str | None = None
    parsed: ModelT | str = "ответ модели не получен"
    if mode == "json_schema":
        try:
            reply = await llm.complete(
                messages,
                model=model,
                temperature=temperature,
                json_schema=schema,
                reasoning_effort=reasoning_effort,
            )
        except httpx.HTTPStatusError as error:
            if not error.response.is_client_error:
                raise
        else:
            if not _THINK.sub("", reply).strip():
                raise EmptyReplyError("Модель вернула пустой ответ")
            parsed = _parse(reply, model_cls)
    if isinstance(parsed, str):
        conversation = [*messages, _fallback_instruction(schema)]
        # Провайдеру без json_schema клиент передаст из схемы то, что тот принимает.
        json_schema = None if mode == "json_schema" else schema
        reply = await llm.complete(
            conversation,
            model=model,
            temperature=temperature,
            json_schema=json_schema,
            reasoning_effort=reasoning_effort,
        )
        parsed = _parse(reply, model_cls)

    last_valid = None if isinstance(parsed, str) else parsed
    for repair in range(repairs + 1):
        if isinstance(parsed, str):
            problems = [parsed]
        else:
            problems = check(parsed) if check is not None else []
            if not problems:
                return parsed
        if repair == repairs:
            break
        if repair_log is not None:
            repair_log.append("; ".join(problems))
        try:
            reply = await llm.complete(
                [*conversation, _repair_instruction(problems)],
                model=model,
                temperature=temperature,
                json_schema=json_schema,
                reasoning_effort=reasoning_effort,
            )
        except httpx.HTTPStatusError as error:
            if not error.response.is_client_error:
                raise
            break
        parsed = _parse(reply, model_cls)
        if not isinstance(parsed, str):
            last_valid = parsed
    if last_valid is not None:
        return last_valid
    raise StructuredOutputError(f"Ответ модели не соответствует схеме: {(reply or '')[:200]!r}")
