"""Набор ответов на эссе с настоящим судьёй. Основная проверка — test_adversarial.py на подделках.

Включает TASKGEN_LLM_SMOKE=1; модель судьи и адрес — из настроек (TASKGEN_LLM__JUDGE_MODEL,
TASKGEN_LLM__BASE_URL). Условие собирается из фактов и вопросов шаблона без модели генерации,
поэтому тест вызывает только судью. Полный прогон с экземплярами от модели и песочницей —
scripts/adversarial_eval.py.
"""

from __future__ import annotations

import os

import httpx
import pytest

from taskgen.config import Settings
from taskgen.contracts import JudgeRequest, TaskKind
from taskgen.evaluation.judge import LLMJudge
from taskgen.llm import LimitedLLM, OpenAICompatibleClient
from tests.evaluation.adversarial.dataset import check, load_sets

pytestmark = pytest.mark.integration

ESSAYS = [item for item in load_sets() if item.spec().kind is TaskKind.ESSAY]


@pytest.fixture
async def judge():
    # Один ответ — judge_samples вызовов модели, на локальной модели это минуты.
    if not os.environ.get("TASKGEN_LLM_SMOKE"):
        pytest.skip("smoke-тест на настоящей модели включает TASKGEN_LLM_SMOKE=1")
    settings = Settings()
    try:
        async with httpx.AsyncClient(timeout=3) as http:
            (await http.get(f"{settings.llm.base_url}/models")).raise_for_status()
    except httpx.HTTPError:
        pytest.skip(f"LLM недоступна по адресу {settings.llm.base_url}")
    client = OpenAICompatibleClient(settings.llm.model_copy(update={"timeout_seconds": 600}))
    yield LLMJudge(
        LimitedLLM(client, limit=settings.llm.concurrency),
        model=settings.llm.judge_model,
        samples=settings.scoring.judge_samples,
        temperature=settings.scoring.judge_temperature,
        reasoning_effort=settings.llm.judge_reasoning_effort,
    )
    await client.aclose()


@pytest.mark.parametrize("answer_set", ESSAYS, ids=[item.name for item in ESSAYS])
async def test_real_judge_keeps_essay_scores_in_expected_ranges(judge, answer_set):
    spec = answer_set.spec()
    statement = answer_set.statement_from_facts()
    scores: dict[str, float] = {}
    problems = []
    for answer in answer_set.answers:
        result = await judge.judge(
            JudgeRequest(
                kind=TaskKind.ESSAY, statement=statement, answer=answer.content, rubric=spec.rubric
            )
        )
        scores[answer.id] = result.score
        verdict = check(answer, result.score, scores)
        print(f"\n{answer.id} ({answer.cls}): {result.score:.3f}, spread {result.spread:.3f}")
        problems += [f"{answer.id}: {problem}" for problem in verdict.problems]
    assert not problems, problems
