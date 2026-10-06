"""Общий ограничитель вызовов LLM: генерация и оценка делят один лимит."""

import asyncio
from types import SimpleNamespace

from taskgen.config import LLMSettings, Settings
from taskgen.contracts import ChatMessage
from taskgen.evaluation.service import Evaluator
from taskgen.generation.service import Generator
from taskgen.llm import LimitedLLM, limited_llm


class SlowLLM:
    def __init__(self) -> None:
        self.active = 0
        self.peak = 0

    async def complete(
        self, messages, *, model=None, temperature=0.7, json_schema=None, reasoning_effort=None
    ) -> str:
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return f"{model}:{temperature}:{json_schema}:{reasoning_effort}"


def _state(**fields):
    return SimpleNamespace(
        settings=Settings(llm=LLMSettings(concurrency=3)), sessionmaker=None, **fields
    )


async def test_limited_llm_caps_concurrent_calls():
    inner = SlowLLM()
    llm = LimitedLLM(inner, limit=2)
    message = [ChatMessage(role="user", content="x")]
    await asyncio.gather(*(llm.complete(message) for _ in range(7)))
    assert inner.peak == 2


def test_one_limiter_in_state_until_llm_is_replaced():
    state = _state(llm=SlowLLM())
    first = limited_llm(state)
    assert isinstance(first, LimitedLLM)
    assert state.limited_llm is first
    assert limited_llm(state) is first
    assert first._semaphore._value == 3
    state.llm = SlowLLM()
    assert limited_llm(state).inner is state.llm


def test_generation_and_evaluation_share_the_limiter():
    state = _state(llm=SlowLLM())
    assert Generator.from_state(state).llm is Evaluator.from_state(state).llm
    assert Evaluator.from_state(state).llm is state.limited_llm


async def test_limited_llm_passes_arguments_through():
    llm = LimitedLLM(SlowLLM(), limit=1)
    reply = await llm.complete(
        [], model="m", temperature=0.1, json_schema={"a": 1}, reasoning_effort="low"
    )
    assert reply == "m:0.1:{'a': 1}:low"
