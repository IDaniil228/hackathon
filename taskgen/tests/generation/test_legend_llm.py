"""Smoke-тест легенды на настоящей модели. Основная проверка — на подделках в test_instance.py."""

import os

import httpx
import pytest

from taskgen.config import GenerationSettings, LLMSettings, SandboxSettings
from taskgen.contracts import RunLimits
from taskgen.evaluation.limits import LimitedLLM
from taskgen.generation.instance import InstanceDeps, generate_instance
from taskgen.llm import OpenAICompatibleClient
from tests.generation.fakes import LocalSandbox
from tests.generation.templates import ENTRYPOINT, code_spec

pytestmark = pytest.mark.integration


@pytest.fixture
async def llm():
    # Одна легенда на локальной qwen3.5:9b пишется минуты, поэтому тест запускается явно.
    if not os.environ.get("TASKGEN_LLM_SMOKE"):
        pytest.skip("smoke-тест на настоящей модели включает TASKGEN_LLM_SMOKE=1")
    settings = LLMSettings()
    try:
        async with httpx.AsyncClient(timeout=3) as http:
            (await http.get(f"{settings.base_url}/models")).raise_for_status()
    except httpx.HTTPError:
        pytest.skip(f"LLM недоступна по адресу {settings.base_url}")
    # Модель с рассуждениями пишет условие дольше таймаута по умолчанию.
    client = OpenAICompatibleClient(settings.model_copy(update={"timeout_seconds": 600}))
    yield LimitedLLM(client, limit=1), settings.generator_model
    await client.aclose()


async def test_real_model_writes_checked_legend(llm):
    client, model = llm
    sandbox = SandboxSettings()
    deps = InstanceDeps(
        llm=client,
        sandbox=LocalSandbox(),
        model=model,
        options=GenerationSettings(),
        limits=RunLimits(
            timeout_ms=sandbox.run_timeout_ms, memory_bytes=sandbox.run_memory_limit_bytes
        ),
    )
    instance = await generate_instance(code_spec(), 2026, deps)
    # Проверки утечки и имени функции уже прошли внутри; здесь — что текст не пустой и по делу.
    assert ENTRYPOINT in instance.statement
    assert len(instance.statement) > 40
    assert instance.public.title.strip()
