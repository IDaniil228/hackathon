"""Тело запроса к OpenAI-совместимому API и разбор ответа без сети: httpx.MockTransport."""

import json
from types import SimpleNamespace

import httpx

from taskgen.config import LLMSettings, Settings
from taskgen.contracts import ChatMessage, JudgeRequest, RubricItem, TaskKind
from taskgen.evaluation.service import Evaluator
from taskgen.generation.service import Generator
from taskgen.llm import LimitedLLM, OpenAICompatibleClient
from tests.evaluation.fakes import FakeLLM

MESSAGES = [ChatMessage(role="user", content="Привет")]


def _client(content):
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://llm")
    return OpenAICompatibleClient(LLMSettings(), http=http), bodies


async def test_reasoning_effort_is_sent_only_when_set():
    client, bodies = _client("ответ")
    await client.complete(MESSAGES, model="m")
    await client.complete(MESSAGES, model="m", reasoning_effort="none")
    assert "reasoning_effort" not in bodies[0]
    assert bodies[1]["reasoning_effort"] == "none"


async def test_missing_content_after_reasoning_is_empty_string():
    # Модель с рассуждениями может истратить ответ на них и вернуть content: null.
    client, _ = _client(None)
    assert await client.complete(MESSAGES) == ""


async def test_limiter_passes_reasoning_effort_through():
    inner = FakeLLM(["ок"])
    await LimitedLLM(inner, limit=1).complete(MESSAGES, reasoning_effort="low")
    assert inner.calls[0]["reasoning_effort"] == "low"


async def test_roles_take_their_own_reasoning_effort():
    llm = LLMSettings(generator_reasoning_effort="none", judge_reasoning_effort="high")
    state = SimpleNamespace(settings=Settings(llm=llm), sessionmaker=None, llm=FakeLLM([]))
    assert Generator.from_state(state).instance_deps().reasoning_effort == "none"

    reply = json.dumps({"items": [{"item_id": "a", "verdict": "no", "quotes": []}]})
    state.llm = FakeLLM(lambda _m, _s: reply)
    judge = Evaluator.from_state(state).judge
    request = JudgeRequest(
        kind=TaskKind.ESSAY, statement="?", answer="!", rubric=[RubricItem(id="a", text="t")]
    )
    await judge.judge(request)
    assert {call["reasoning_effort"] for call in state.llm.calls} == {"high"}
