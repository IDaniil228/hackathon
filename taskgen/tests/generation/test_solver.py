"""Слепой решатель задания с кодом: решение по публичной части идёт на все кейсы экземпляра."""

from __future__ import annotations

import json

import pytest

from taskgen.config import GenerationSettings
from taskgen.contracts import RunLimits
from taskgen.generation.instance import (
    InstanceDeps,
    LLMUnavailableError,
    ModelOutputError,
    SandboxUnavailableError,
    generate_instance,
)
from taskgen.sandbox.errors import SandboxServiceError
from tests.evaluation.fakes import FakeLLM, http_error
from tests.generation.fakes import LocalSandbox, code_solver_reply, connect_error
from tests.generation.templates import ENTRYPOINT, REFERENCE, code_spec

MODEL = "generator-model"
SOLVER = "solver-model"
LIMITS = RunLimits(timeout_ms=5000, memory_bytes=256 * 1024 * 1024)
GOOD = REFERENCE.removeprefix("```python\n").removesuffix("\n```")
# Сумма k первых, а не k наибольших: на случайных входах не совпадает с ответом.
WRONG = "def top_sum(nums, k):\n    return sum(nums[:k])\n"
STATEMENT = f"Напишите функцию {ENTRYPOINT}(nums, k): сумма k наибольших чисел."


def deps(llm, sandbox, **options) -> InstanceDeps:
    return InstanceDeps(
        llm=llm,
        sandbox=sandbox,
        model=MODEL,
        options=GenerationSettings(**options),
        limits=LIMITS,
        solver_model=SOLVER,
    )


def model(solution: str):
    def respond(messages, schema):
        reply = code_solver_reply(messages, schema, solution=solution)
        if reply is not None:
            return reply
        return json.dumps({"title": "Склад", "statement": STATEMENT}, ensure_ascii=False)

    return respond


async def test_solver_solution_runs_on_all_cases_and_is_recorded():
    sandbox = LocalSandbox()
    llm = FakeLLM(model(GOOD))
    instance = await generate_instance(code_spec(), 4, deps(llm, sandbox, solver_enabled=True))

    assert [call["model"] for call in llm.calls] == [MODEL, SOLVER]
    # Решатель видит условие, сигнатуру и примеры, но не эталон и не скрытые кейсы.
    prompt = "\n".join(message.content for message in llm.calls[1]["messages"])
    assert STATEMENT in prompt and ENTRYPOINT in prompt
    shown = len(instance.public.examples)
    for case in instance.hidden.cases[:shown]:
        assert json.dumps(case.input, ensure_ascii=False) in prompt
    for case in instance.hidden.cases[shown:]:
        assert json.dumps(case.input, ensure_ascii=False) not in prompt
    assert "sorted(nums" not in prompt
    # Прогон решателя — на всех кейсах экземпляра, включая скрытые.
    solver_run = sandbox.test_runs[-1]
    assert solver_run.solution == GOOD
    assert solver_run.cases == instance.hidden.cases
    assert instance.hidden_dump()["solver"] == {
        "passed": True,
        "attempts": 1,
        "rejected_drafts": 0,
    }


async def test_solver_disabled_by_default_makes_no_call():
    llm = FakeLLM(model(GOOD))
    instance = await generate_instance(code_spec(), 4, deps(llm, LocalSandbox()))
    assert len(llm.calls) == 1
    assert instance.hidden.solver is None


async def test_unsolvable_statement_is_rewritten_without_hidden_data_then_model_error():
    sandbox = LocalSandbox()
    llm = FakeLLM(model(WRONG))
    with pytest.raises(ModelOutputError, match="по условию нельзя решить"):
        await generate_instance(
            code_spec(), 4, deps(llm, sandbox, solver_enabled=True, legend_attempts=2)
        )
    # Легенда, решатель, легенда, решатель; вторая легенда знает причину отказа.
    assert [call["model"] for call in llm.calls] == [MODEL, SOLVER, MODEL, SOLVER]
    retry = llm.calls[2]["messages"][-1].content
    assert "по условию нельзя решить" in retry
    hidden = sandbox.test_runs[0].cases[2:]
    for case in hidden:
        assert json.dumps(case.input, ensure_ascii=False) not in retry
        assert str(case.expected) not in retry


async def test_solver_model_error_means_no_instance():
    def respond(messages, schema):
        if "solution" in (schema or {}).get("properties", {}):
            raise http_error(503)
        return json.dumps({"title": "Склад", "statement": STATEMENT}, ensure_ascii=False)

    with pytest.raises(LLMUnavailableError):
        await generate_instance(
            code_spec(), 4, deps(FakeLLM(respond), LocalSandbox(), solver_enabled=True)
        )


# Верное решение, текст которого отличается от эталона: песочница отличает прогон решателя.
OTHER_GOOD = "def top_sum(nums, k):\n    return sum(sorted(nums)[len(nums) - k:])\n"


class DownOnSolver(LocalSandbox):
    """Песочница, которая прогоняет эталон, но падает на решении решателя."""

    def __init__(self, failure) -> None:
        super().__init__()
        self.failure = failure

    async def run_tests(self, request):
        if request.solution == OTHER_GOOD:
            raise self.failure()
        return await super().run_tests(request)


# PistonRunner сообщает о недоступном или отказавшем Piston через SandboxServiceError, а
# подделки песочницы бросают сетевую ошибку httpx.
@pytest.mark.parametrize(
    "failure",
    [connect_error, lambda: SandboxServiceError("Piston недоступен")],
    ids=["http", "piston"],
)
async def test_sandbox_down_during_solver_run_means_no_instance(failure):
    sandbox = DownOnSolver(failure)
    with pytest.raises(SandboxUnavailableError):
        await generate_instance(
            code_spec(), 4, deps(FakeLLM(model(OTHER_GOOD)), sandbox, solver_enabled=True)
        )
    # Эталон прогнан: сбой пришёлся именно на решение решателя.
    assert len(sandbox.test_runs) == 1
