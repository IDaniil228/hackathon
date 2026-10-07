import json

import pytest
from pydantic import BaseModel

from taskgen.contracts import ChatMessage
from taskgen.evaluation.jsonout import (
    StructuredOutputError,
    complete_structured,
    extract_json_object,
)
from tests.evaluation.fakes import FakeLLM, http_error


class Point(BaseModel):
    x: int
    y: int


SCHEMA = Point.model_json_schema()
MESSAGES = [ChatMessage(role="user", content="Дай точку")]


def test_extract_plain_json():
    assert extract_json_object('{"x": 1}') == {"x": 1}


def test_extract_prefers_fenced_block():
    text = 'Вот пример {"x": 0} и ответ:\n```json\n{"x": 2, "y": 3}\n```\nГотово'
    assert extract_json_object(text) == {"x": 2, "y": 3}


def test_extract_first_balanced_object_from_prose():
    text = (
        'Думаю так: {"note": "скобка } внутри строки", "inner": {"a": [1, {"b": 2}]}}'
        ' конец {"x": 9}'
    )
    assert extract_json_object(text) == {
        "note": "скобка } внутри строки",
        "inner": {"a": [1, {"b": 2}]},
    }


def test_extract_skips_reasoning_block():
    text = '<think>может {"x": 0}?</think>{"x": 5}'
    assert extract_json_object(text) == {"x": 5}


def test_extract_skips_unparsable_candidate_and_takes_next():
    text = 'Схема {x: int}, а ответ {"x": 1, "y": 2}'
    assert extract_json_object(text) == {"x": 1, "y": 2}


def test_extract_without_object_raises():
    with pytest.raises(ValueError):
        extract_json_object("нет тут JSON")


async def test_structured_uses_json_schema_first():
    llm = FakeLLM(['{"x": 1, "y": 2}'])
    point = await complete_structured(llm, MESSAGES, Point, SCHEMA, model="m", temperature=0.5)
    assert point == Point(x=1, y=2)
    assert len(llm.calls) == 1
    assert llm.calls[0]["json_schema"] == SCHEMA
    assert llm.calls[0]["model"] == "m"


async def test_structured_falls_back_when_provider_rejects_response_format():
    def responder(messages, schema):
        if schema is not None:
            raise http_error(400)
        return 'Конечно! Вот ответ: {"x": 3, "y": 4}. Обращайтесь'

    llm = FakeLLM(responder)
    point = await complete_structured(llm, MESSAGES, Point, SCHEMA, model="m", temperature=0.5)
    assert point == Point(x=3, y=4)
    fallback = llm.calls[1]
    assert fallback["json_schema"] is None
    # Без json_schema модель узнаёт схему из текста запроса.
    assert json.dumps(SCHEMA["properties"], ensure_ascii=False) in fallback["messages"][-1].content


async def test_structured_falls_back_when_reply_does_not_match_schema():
    llm = FakeLLM(['{"x": "не число"}', 'ответ: {"x": 1, "y": 1}'])
    point = await complete_structured(llm, MESSAGES, Point, SCHEMA, model="m", temperature=0.5)
    assert point == Point(x=1, y=1)


async def test_structured_does_not_hide_server_errors():
    def responder(messages, schema):
        raise http_error(503)

    with pytest.raises(Exception, match="ошибка провайдера"):
        await complete_structured(FakeLLM(responder), MESSAGES, Point, SCHEMA, model="m")


async def test_structured_raises_when_both_attempts_fail():
    llm = FakeLLM(["мусор", "снова мусор"])
    with pytest.raises(StructuredOutputError):
        await complete_structured(llm, MESSAGES, Point, SCHEMA, model="m")


async def test_empty_reply_is_invalid_without_fallback_call():
    # Пустой content — модель истратила ответ на рассуждения; повтор без схемы этого не
    # исправит, а на слабой машине стоит ещё минуты. Решение о повторе — за вызывающим.
    llm = FakeLLM(["", '{"x": 1, "y": 1}'])
    with pytest.raises(StructuredOutputError, match="пуст"):
        await complete_structured(llm, MESSAGES, Point, SCHEMA, model="m")
    assert len(llm.calls) == 1


async def test_reasoning_effort_reaches_both_attempts():
    llm = FakeLLM(["мусор", '{"x": 1, "y": 1}'])
    await complete_structured(llm, MESSAGES, Point, SCHEMA, model="m", reasoning_effort="none")
    assert [call["reasoning_effort"] for call in llm.calls] == ["none", "none"]


class SchemalessLLM(FakeLLM):
    """Провайдер без json_schema: сообщает свой режим структурированного ответа."""

    def __init__(self, replies: list[str], mode: str) -> None:
        super().__init__(replies)
        self.mode = mode
        self.asked: list[str | None] = []

    def structured_output(self, model: str | None) -> str:
        self.asked.append(model)
        return self.mode


@pytest.mark.parametrize("mode", ["json_object", "text"])
async def test_provider_without_json_schema_gets_the_fallback_request_first(mode):
    # Иначе каждый вызов к такому провайдеру сначала получает 4xx и только потом повтор.
    llm = SchemalessLLM(['{"x": 1, "y": 2}'], mode)
    point = await complete_structured(llm, MESSAGES, Point, SCHEMA, model="deepseek/m")
    assert point == Point(x=1, y=2)
    assert llm.asked == ["deepseek/m"]
    (call,) = llm.calls
    assert json.dumps(SCHEMA, ensure_ascii=False) in call["messages"][-1].content
    # Схема всё равно передаётся: клиент сам решает, что из неё отправить провайдеру.
    assert call["json_schema"] == SCHEMA


async def test_provider_without_json_schema_gets_one_attempt():
    llm = SchemalessLLM(["мусор", '{"x": 1, "y": 2}'], "json_object")
    with pytest.raises(StructuredOutputError):
        await complete_structured(llm, MESSAGES, Point, SCHEMA, model="deepseek/m")
    assert len(llm.calls) == 1
