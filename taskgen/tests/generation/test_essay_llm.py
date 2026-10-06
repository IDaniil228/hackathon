"""Проверяльщик фактов эссе на настоящей модели. Основная проверка — на подделке в test_essay.py.

Модель задаёт TASKGEN_ESSAY_CHECK_MODEL, адрес — TASKGEN_LLM__BASE_URL.
"""

import os

import httpx
import pytest

from taskgen.config import Settings
from taskgen.generation.essay import check_essay_scenario
from taskgen.llm import LimitedLLM, OpenAICompatibleClient
from tests.generation.test_essay import PARAMS, bad_scenario, leakage_spec

pytestmark = pytest.mark.integration


@pytest.fixture
async def llm():
    # Один вызов на локальной модели с рассуждениями идёт минуты, поэтому тест явный.
    if not os.environ.get("TASKGEN_LLM_SMOKE"):
        pytest.skip("smoke-тест на настоящей модели включает TASKGEN_LLM_SMOKE=1")
    model = os.environ.get("TASKGEN_ESSAY_CHECK_MODEL")
    if not model:
        pytest.skip("модель проверяльщика задаёт TASKGEN_ESSAY_CHECK_MODEL")
    settings = Settings().llm
    try:
        async with httpx.AsyncClient(timeout=3) as http:
            (await http.get(f"{settings.base_url}/models")).raise_for_status()
    except httpx.HTTPError:
        pytest.skip(f"LLM недоступна по адресу {settings.base_url}")
    client = OpenAICompatibleClient(settings.model_copy(update={"timeout_seconds": 900}))
    yield LimitedLLM(client, limit=1), model
    await client.aclose()


async def test_real_checker_finds_lost_tuning_fact(llm):
    client, model = llm

    result = await check_essay_scenario(client, leakage_spec(), bad_scenario(), PARAMS, model=model)

    print(f"\nмодель {model}: {result}")
    assert not result.ok
    assert any(fact.startswith("tuning") for fact in result.missing_facts), result
