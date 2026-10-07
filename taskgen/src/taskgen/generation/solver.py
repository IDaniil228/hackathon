"""Слепой решатель: решает экземпляр до выдачи по тому, что увидит кандидат.

Эталон доказывает, что задача решаема при знании шаблона. Решатель проверяет другое: что
решение следует из самого условия. Задачку он решает по кейсу, вопросу и вариантам без
ключа и фактов; задание с кодом — по условию, сигнатуре, примерам и окружению без эталона и
скрытых кейсов, а его решение идёт на все кейсы экземпляра. Решатель опционален: слабая
модель отклоняет годные экземпляры, и включать его стоит с сильной моделью.

Сетевые ошибки и ошибки провайдера не перехватываются: экземпляр без вердикта решателя не
выдаётся.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel

from taskgen.contracts import (
    ChatMessage,
    IOKind,
    LLMClient,
    OutputCheck,
    ReasoningEffort,
    RunLimits,
    SandboxRunner,
    TestCase,
    TestRunRequest,
)
from taskgen.evaluation.jsonout import StructuredOutputError, complete_structured
from taskgen.sandbox.errors import SandboxServiceError

# Немного случайности: при solver_attempts > 1 повторная попытка не повторяет первую.
SOLVER_TEMPERATURE = 0.3


class SolverSandboxError(Exception):
    """Песочница не ответила на прогон решения решателя: это не вердикт о задании."""


@dataclass(frozen=True)
class SolverConfig:
    llm: LLMClient
    model: str
    attempts: int
    reasoning_effort: ReasoningEffort | None = None


@dataclass(frozen=True)
class SolverVerdict:
    passed: bool
    attempts: int
    # Причина для писателя условия при отказе; скрытых данных в ней нет.
    reason: str = ""


# --- Тестовая задачка ----------------------------------------------------------------------

QUIZ_SYSTEM = (
    "Ты кандидат на техническом тесте. Прочитай кейс и вопрос и выбери один вариант ответа "
    "по его id. Отдельно перечисли id всех вариантов, которые верно отвечают на вопрос в "
    "точности, с учётом его условий вроде «минимальный» или «главный»: вариант, который "
    "только не противоречит кейсу, но не отвечает на такой вопрос, не верен. Если верен "
    "только выбранный, список из одного id. Верни JSON с полями choice и correct."
)


class _QuizReply(BaseModel):
    choice: str
    correct: list[str]


def _quiz_messages(statement: str, options: Sequence[tuple[str, str]]) -> list[ChatMessage]:
    listing = "\n".join(f"- {option_id}: {text}" for option_id, text in options)
    return [
        ChatMessage(role="system", content=QUIZ_SYSTEM),
        ChatMessage(role="user", content=f"{statement}\n\nВарианты ответа:\n{listing}"),
    ]


def _quiz_schema(ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["choice", "correct"],
        "properties": {
            "choice": {"type": "string", "enum": ids},
            "correct": {"type": "array", "items": {"type": "string", "enum": ids}},
        },
    }


async def solve_quiz(
    config: SolverConfig, statement: str, options: Sequence[tuple[str, str]], key: str
) -> SolverVerdict:
    """Годен ли кейс: решатель выбрал ключ и верным назвал только его.

    Решатель видит текст условия и варианты (id, текст), но не key.
    """
    ids = [option_id for option_id, _ in options]
    reason = ""
    for attempt in range(1, config.attempts + 1):
        try:
            reply = await complete_structured(
                config.llm,
                _quiz_messages(statement, options),
                _QuizReply,
                _quiz_schema(ids),
                model=config.model,
                temperature=SOLVER_TEMPERATURE,
                reasoning_effort=config.reasoning_effort,
            )
        except StructuredOutputError:
            reason = "решатель не ответил по схеме, кейс не проверен"
            continue
        if reply.choice == key and set(reply.correct) == {key}:
            return SolverVerdict(passed=True, attempts=attempt)
        if reply.choice != key:
            reason = (
                "решатель по тексту кейса выбрал другой ответ: из кейса ответ не следует "
                "однозначно; опиши факты точнее"
            )
        else:
            reason = (
                "решатель счёл верными несколько ответов: кейс допускает второй верный "
                "ответ; опиши факты так, чтобы он был исключён"
            )
    return SolverVerdict(passed=False, attempts=config.attempts, reason=reason)


# --- Задание с кодом -----------------------------------------------------------------------

CODE_SYSTEM = (
    "Ты кандидат на техническом тесте. Реши задание только по его условию и примерам. "
    "Напиши функцию с указанным именем целиком, без кода вне функций, кроме импортов и "
    "вспомогательных определений. Верни JSON с полем solution — исходный код решения."
)


class _CodeReply(BaseModel):
    solution: str


CODE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["solution"],
    "properties": {"solution": {"type": "string", "description": "Исходный код решения"}},
}


def _code_messages(
    statement: str, entrypoint: str, runtime: str, environment: str, examples: list[TestCase]
) -> list[ChatMessage]:
    shown = "\n".join(
        f"- input: {json.dumps(case.input, ensure_ascii=False)}; "
        f"expected: {json.dumps(case.expected, ensure_ascii=False)}"
        for case in examples
    )
    user = (
        f"{statement}\n\nФункция: `{entrypoint}`, окружение {runtime}. {environment}.\n\n"
        f"Примеры (input — аргументы функции, expected — результат):\n{shown}"
    )
    return [
        ChatMessage(role="system", content=CODE_SYSTEM),
        ChatMessage(role="user", content=user),
    ]


async def solve_code(
    config: SolverConfig,
    sandbox: SandboxRunner,
    *,
    statement: str,
    entrypoint: str,
    runtime: str,
    environment: str,
    examples: list[TestCase],
    cases: list[TestCase],
    limits: RunLimits,
) -> SolverVerdict:
    """Годно ли условие: решение, написанное только по публичной части, проходит все кейсы.

    Причина отказа называет только число непройденных кейсов: писатель условия не видел
    скрытых кейсов и не должен увидеть их через причину.
    """
    reason = ""
    for attempt in range(1, config.attempts + 1):
        try:
            reply = await complete_structured(
                config.llm,
                _code_messages(statement, entrypoint, runtime, environment, examples),
                _CodeReply,
                CODE_SCHEMA,
                model=config.model,
                temperature=SOLVER_TEMPERATURE,
                reasoning_effort=config.reasoning_effort,
            )
        except StructuredOutputError:
            reason = "решатель не ответил по схеме, условие не проверено"
            continue
        request = TestRunRequest(
            runtime=runtime,
            io=IOKind.FUNCTION,
            entrypoint=entrypoint,
            solution=reply.solution,
            cases=cases,
            check=OutputCheck(mode="json"),
            limits=limits,
        )
        try:
            report = await sandbox.run_tests(request)
        # PistonRunner сообщает об отказе Piston через SandboxServiceError, а не через httpx.
        except (httpx.HTTPError, SandboxServiceError) as error:
            raise SolverSandboxError("Песочница недоступна для решения решателя") from error
        failed = sum(not outcome.passed for outcome in report.outcomes)
        if report.compile_error is None and report.outcomes and failed == 0:
            return SolverVerdict(passed=True, attempts=attempt)
        if report.compile_error is not None:
            reason = "по условию нельзя решить: решение решателя не скомпилировалось"
        else:
            reason = (
                f"по условию нельзя решить: решение решателя не прошло {failed} проверок из "
                f"{len(cases)}; проверь, что условие полно описывает вход, выход и граничные "
                "случаи"
            )
    return SolverVerdict(passed=False, attempts=config.attempts, reason=reason)
