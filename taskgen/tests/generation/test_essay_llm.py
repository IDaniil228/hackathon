"""Проверяльщик фактов эссе на настоящей модели. Основная проверка — на подделке в test_essay.py.

Модель и провайдер — роль llm.checker из настроек, например TASKGEN_LLM__CHECKER__MODEL.
"""

import os

import pytest

from taskgen.config import Settings
from taskgen.generation.essay import check_essay_scenario
from tests.generation.test_essay import PARAMS, bad_scenario, leakage_spec
from tests.live_llm import live_router

pytestmark = pytest.mark.integration


@pytest.fixture
async def llm():
    # Один вызов на локальной модели с рассуждениями идёт минуты, поэтому тест явный.
    if not os.environ.get("TASKGEN_LLM_SMOKE"):
        pytest.skip("smoke-тест на настоящей модели включает TASKGEN_LLM_SMOKE=1")
    settings = Settings().llm
    router = await live_router(settings, ("checker",))
    yield router, settings.checker.model
    await router.aclose()


async def test_real_checker_finds_lost_tuning_fact(llm):
    client, model = llm

    result = await check_essay_scenario(
        client,
        leakage_spec(),
        bad_scenario(),
        PARAMS,
        model=model,
        evidence_options=Settings().evidence,
    )

    print(f"\nмодель {model}: {result}")
    assert not result.ok
    assert any(fact.startswith("tuning") for fact in result.missing_facts), result
