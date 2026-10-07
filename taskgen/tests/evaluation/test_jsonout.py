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


# --- Повтор с указанием ошибки -------------------------------------------------------------


def positive(point: Point) -> list[str]:
    return [] if point.x > 0 else [f"x = {point.x}, а нужно положительное"]


async def test_reply_rejected_by_check_is_asked_again_with_the_problem():
    llm = FakeLLM(['{"x": -1, "y": 0}', '{"x": 2, "y": 0}'])
    log: list[str] = []
    point = await complete_structured(
        llm, MESSAGES, Point, SCHEMA, model="m", check=positive, repairs=2, repair_log=log
    )
    assert point == Point(x=2, y=0)
    assert len(llm.calls) == 2
    second = llm.calls[1]["messages"]
    # Повтор — тот же запрос и то, что в прошлом ответе не так; сам ответ запрос не удлиняет.
    assert second[:-1] == MESSAGES
    assert second[-1].role == "user" and "x = -1" in second[-1].content
    assert log == ["x = -1, а нужно положительное"]


async def test_repair_rejected_by_provider_returns_the_last_parsed_reply():
    # Ollama отвечает 400, когда запрос не влезает в окно контекста.
    def respond(messages, _schema):
        if len(messages) > len(MESSAGES):
            raise http_error(400)
        return '{"x": -1, "y": 0}'

    llm = FakeLLM(respond)
    point = await complete_structured(
        llm, MESSAGES, Point, SCHEMA, model="m", check=positive, repairs=2
    )
    assert point == Point(x=-1, y=0)
    assert len(llm.calls) == 2


async def test_repair_does_not_hide_server_errors():
    def respond(messages, _schema):
        if len(messages) > len(MESSAGES):
            raise http_error(503)
        return '{"x": -1, "y": 0}'

    with pytest.raises(Exception, match="ошибка провайдера"):
        await complete_structured(
            FakeLLM(respond), MESSAGES, Point, SCHEMA, model="m", check=positive, repairs=1
        )


async def test_repairs_stop_after_the_limit_and_return_the_last_reply():
    llm = FakeLLM(lambda _m, _s: '{"x": -1, "y": 0}')
    log: list[str] = []
    point = await complete_structured(
        llm, MESSAGES, Point, SCHEMA, model="m", check=positive, repairs=2, repair_log=log
    )
    # Решение, что делать с ответом, который так и не прошёл проверку, — за вызывающим.
    assert point == Point(x=-1, y=0)
    assert len(llm.calls) == 3
    assert len(log) == 2


async def test_check_without_repairs_makes_one_call():
    llm = FakeLLM(lambda _m, _s: '{"x": -1, "y": 0}')
    point = await complete_structured(llm, MESSAGES, Point, SCHEMA, model="m", check=positive)
    assert point == Point(x=-1, y=0)
    assert len(llm.calls) == 1


async def test_reply_out_of_schema_is_asked_again_with_the_error():
    llm = FakeLLM(["мусор", '{"x": "не число"}', '{"x": 1, "y": 1}'])
    point = await complete_structured(llm, MESSAGES, Point, SCHEMA, model="m", repairs=1)
    assert point == Point(x=1, y=1)
    assert len(llm.calls) == 3
    repair = llm.calls[2]["messages"]
    # Повтор идёт в разговор без схемы, где модели уже показана схема в тексте.
    assert repair[:-1] == llm.calls[1]["messages"]
    assert repair[-1].role == "user" and "x" in repair[-1].content


async def test_reply_out_of_schema_after_all_repairs_raises():
    llm = FakeLLM(lambda _m, _s: "мусор")
    with pytest.raises(StructuredOutputError):
        await complete_structured(llm, MESSAGES, Point, SCHEMA, model="m", repairs=2)
    assert len(llm.calls) == 4


async def test_empty_reply_is_not_repaired():
    llm = FakeLLM(["", '{"x": 1, "y": 1}'])
    with pytest.raises(StructuredOutputError, match="пуст"):
        await complete_structured(llm, MESSAGES, Point, SCHEMA, model="m", repairs=2)
    assert len(llm.calls) == 1


async def test_reasoning_effort_reaches_both_attempts():
    llm = FakeLLM(["мусор", '{"x": 1, "y": 1}'])
    await complete_structured(llm, MESSAGES, Point, SCHEMA, model="m", reasoning_effort="none")
    assert [call["reasoning_effort"] for call in llm.calls] == ["none", "none"]
