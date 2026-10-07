"""Проверяльщик machine summary на настоящей модели: регрессионный набор черновиков.

Набор — summary_drafts.json: черновики сводки разных моделей по фактам сквозного прогона
и разметка, верен ли черновик. Модель и провайдер — роль llm.checker из настроек, способ
доказательства — блок evidence. Основная проверка механики — на подделке в test_summary.py.
"""

import os

import pytest

from taskgen.config import Settings
from taskgen.evaluation.evidence import split_sentences
from taskgen.evaluation.summary import check_summary
from tests.evaluation.test_summary import DRAFTS
from tests.live_llm import live_router

pytestmark = pytest.mark.integration


class _Counted:
    """LLMClient, который считает вызовы: набор платный, и отчёт называет их число."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.calls = 0

    async def complete(self, messages, **kwargs):
        self.calls += 1
        return await self.inner.complete(messages, **kwargs)

    def structured_output(self, model):
        # Без режима провайдера complete_structured идёт в два шага и тратит лишний вызов.
        return self.inner.structured_output(model)


@pytest.fixture
async def checker():
    if not os.environ.get("TASKGEN_LLM_SMOKE"):
        pytest.skip("smoke-тест на настоящей модели включает TASKGEN_LLM_SMOKE=1")
    settings = Settings()
    router = await live_router(settings.llm, ("checker",))
    yield _Counted(router), settings
    await router.aclose()


def _case(draft: dict):
    # Известная ошибка проверяльщика остаётся в наборе и видна в отчёте как xfail. Она
    # замерена на одной модели, поэтому XPASS на другой модели не краснеет.
    marks = (
        [pytest.mark.xfail(reason=draft["checker_miss"], strict=False)]
        if "checker_miss" in draft
        else []
    )
    return pytest.param(draft, marks=marks, id=f"{draft['facts']}-{draft['source']}")


@pytest.mark.parametrize("draft", [_case(draft) for draft in DRAFTS["drafts"]])
async def test_real_checker_matches_labels(checker, draft):
    llm, settings = checker

    result = await check_summary(
        llm,
        split_sentences(draft["draft"]),
        DRAFTS["facts"][draft["facts"]],
        model=settings.llm.checker.model,
        reasoning_effort=settings.llm.checker.reasoning_effort,
        evidence=settings.evidence,
    )

    print(f"\n{settings.llm.checker.model}, вызовов {llm.calls}: {result}")
    assert result.ok == draft["grounded"], result.reasons
