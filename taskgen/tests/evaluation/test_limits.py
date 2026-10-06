import asyncio
from types import SimpleNamespace

from taskgen.config import Settings
from taskgen.contracts import ChatMessage
from taskgen.evaluation.limits import LimitedLLM
from taskgen.evaluation.service import Evaluator
from taskgen.evaluation.settings import EvaluationSettings


class SlowLLM:
    def __init__(self) -> None:
        self.active = 0
        self.peak = 0

    async def complete(self, messages, *, model=None, temperature=0.7, json_schema=None) -> str:
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return f"{model}:{temperature}:{json_schema}"


async def test_limited_llm_caps_concurrent_calls():
    inner = SlowLLM()
    llm = LimitedLLM(inner, limit=2)
    message = [ChatMessage(role="user", content="x")]
    await asyncio.gather(*(llm.complete(message) for _ in range(7)))
    assert inner.peak == 2


def test_evaluator_shares_one_limiter_until_llm_is_replaced():
    state = SimpleNamespace(
        settings=Settings(),
        sessionmaker=None,
        llm=SlowLLM(),
        evaluation_settings=EvaluationSettings(llm_concurrency=3),
    )
    first = Evaluator.from_state(state)
    second = Evaluator.from_state(state)
    assert isinstance(first.llm, LimitedLLM)
    assert first.llm is second.llm
    assert first.llm._semaphore._value == 3
    state.llm = SlowLLM()
    assert Evaluator.from_state(state).llm.inner is state.llm


async def test_limited_llm_passes_arguments_through():
    llm = LimitedLLM(SlowLLM(), limit=1)
    reply = await llm.complete([], model="m", temperature=0.1, json_schema={"a": 1})
    assert reply == "m:0.1:{'a': 1}"
