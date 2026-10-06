"""Тестовая задачка: теоретический вопрос автора в кейсе от LLM и варианты ответа.

Вариант кейса выбирает seed. Факты варианта определяют верный ответ, а разные варианты
одного шаблона ведут к разным ответам, поэтому утёкший ответ не подходит к другому
экземпляру. Кейс пишет LLM по фактам варианта и проверяется так же, как ситуация эссе
(essay.py): все факты есть с цитатами, запрещённых фраз нет. Вопрос и ответы идут
дословно, ответы — в порядке, перемешанном по seed, под непрозрачными id.

Неверный ответ не становится верным из-за кейса только тогда, когда факты варианта
исключают его. Код этого не доказывает: это обязанность автора шаблона, а слепой решатель
(solver.py) ловит неоднозначный кейс, если включён.
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from taskgen.contracts import LLMClient, QuizVariant, ReasoningEffort, SolverRecord, TemplateSpec
from taskgen.generation.essay import EssayScenarioError, generate_essay
from taskgen.generation.params import fill_placeholders
from taskgen.generation.solver import SolverConfig, solve_quiz


class QuizOption(BaseModel):
    id: str = Field(description="Непрозрачный id ответа; его кандидат и присылает")
    text: str


QUIZ_WRITER_RULES = (
    " Это кейс к вопросу с выбором ответа. Не называй ни одного решения, алгоритма или "
    "подхода, который мог бы быть ответом, и не рассуждай о том, что подойдёт. Факты "
    "записаны общими словами вроде «объект» или «запись»: назови их тем, чем они являются "
    "в домене кейса."
)


def choose_variant(spec: TemplateSpec, seed: int) -> QuizVariant:
    """Вариант кейса по seed задания; генератор отдельный от параметров."""
    return random.Random(f"quiz-variant:{seed}").choice(spec.variants)


def shuffled_options(
    variant: QuizVariant, params: dict[str, Any], seed: int
) -> tuple[list[QuizOption], str]:
    """Ответы варианта в порядке по seed и id верного; id — буквы по порядку показа."""
    texts = [variant.correct, *variant.wrong]
    order = list(range(len(texts)))
    random.Random(f"quiz-options:{seed}").shuffle(order)
    options = [
        QuizOption(id=string.ascii_lowercase[place], text=fill_placeholders(texts[index], params))
        for place, index in enumerate(order)
    ]
    return options, options[order.index(0)].id


def case_spec(spec: TemplateSpec, variant: QuizVariant) -> TemplateSpec:
    """Шаблон в форме эссе для проверенной ситуации: вопрос вместо вопросов, факты варианта."""
    return spec.model_copy(update={"questions": spec.question, "scenario_facts": variant.facts})


class QuizCaseError(Exception):
    """Ни один кейс не прошёл проверку фактов, запрещённых фраз или решателя."""


@dataclass(frozen=True)
class QuizDraft:
    title: str
    statement: str
    solver: SolverRecord | None


async def write_case(
    llm: LLMClient,
    spec: TemplateSpec,
    variant: QuizVariant,
    params: dict[str, Any],
    options: list[QuizOption],
    key: str,
    *,
    model: str,
    checker_model: str,
    temperature: float,
    attempts: int,
    reasoning_effort: ReasoningEffort | None = None,
    checker_reasoning_effort: ReasoningEffort | None = None,
    solver: SolverConfig | None = None,
    system_suffix: str = "",
    request_suffix: str = "",
) -> QuizDraft:
    """Проверенный кейс за не больше чем attempts попыток.

    Каждая попытка — один проход generate_essay: кейс и проверка фактов. Решатель, если он
    задан, решает принятый кейс; его отказ, как и отказ проверки, уходит писателю в
    следующей попытке. httpx.HTTPError от любой модели не перехватывается.
    """
    derived = case_spec(spec, variant)
    choices = [(option.id, option.text) for option in options]
    reason = ""
    solver_calls = rejected = 0
    for _ in range(attempts):
        retry = (
            f"\n\nПредыдущий вариант отклонён: {reason}. Перепиши ситуацию целиком."
            if reason
            else ""
        )
        try:
            draft = await generate_essay(
                llm,
                derived,
                params,
                model=model,
                checker_model=checker_model,
                temperature=temperature,
                attempts=1,
                reasoning_effort=reasoning_effort,
                checker_reasoning_effort=checker_reasoning_effort,
                system_suffix=QUIZ_WRITER_RULES + system_suffix,
                request_suffix=request_suffix + retry,
            )
        except EssayScenarioError as error:
            reason = "; ".join(error.check.reasons) if error.check else str(error)
            continue
        if solver is None:
            return QuizDraft(draft.title, draft.statement, None)
        verdict = await solve_quiz(solver, draft.statement, choices, key)
        solver_calls += verdict.attempts
        if verdict.passed:
            record = SolverRecord(passed=True, attempts=solver_calls, rejected_drafts=rejected)
            return QuizDraft(draft.title, draft.statement, record)
        rejected += 1
        reason = verdict.reason
    raise QuizCaseError(
        f"Шаблон {spec.name}: кейс задачки не прошёл проверку за {attempts} попыток; "
        f"последняя причина — {reason}"
    )
