"""Smoke-тест легенды на настоящей модели. Основная проверка — на подделках в test_instance.py."""

import os

import pytest

from taskgen.config import GenerationSettings, SandboxSettings, Settings
from taskgen.contracts import RunLimits
from taskgen.generation.instance import InstanceDeps, generate_instance
from tests.generation.fakes import LocalSandbox
from tests.generation.templates import ENTRYPOINT, code_spec
from tests.live_llm import live_router

pytestmark = pytest.mark.integration


@pytest.fixture
async def llm():
    # Одна легенда на локальной модели пишется минуты, поэтому тест запускается явно.
    if not os.environ.get("TASKGEN_LLM_SMOKE"):
        pytest.skip("smoke-тест на настоящей модели включает TASKGEN_LLM_SMOKE=1")
    settings = Settings().llm
    router = await live_router(settings, ("generator",))
    yield router, settings.generator
    await router.aclose()


async def test_real_model_writes_checked_legend(llm):
    client, role = llm
    sandbox = SandboxSettings()
    deps = InstanceDeps(
        llm=client,
        sandbox=LocalSandbox(),
        model=role.model,
        reasoning_effort=role.reasoning_effort,
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
