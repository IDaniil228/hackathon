"""Экземпляр задания stdio: примеры — вход и вывод программы, сигнатуры функции нет."""

from __future__ import annotations

import json

import pytest

from taskgen.bank.parser import parse_template
from taskgen.config import GenerationSettings
from taskgen.contracts import IOKind, RunLimits
from taskgen.generation.instance import InstanceDeps, ModelOutputError, generate_instance
from tests.bank.factory import STDIO_REFERENCE, make_stdio
from tests.evaluation.fakes import FakeLLM
from tests.generation.fakes import LocalSandbox, code_solver_reply

MODEL = "generator-model"
SOLVER = "solver-model"
LIMITS = RunLimits(timeout_ms=5000, memory_bytes=256 * 1024 * 1024)
STATEMENT = "Программа читает строки чеков из stdin и печатает сумму каждой строки."


def deps(llm, sandbox, **options) -> InstanceDeps:
    return InstanceDeps(
        llm=llm,
        sandbox=sandbox,
        model=MODEL,
        options=GenerationSettings(**options),
        limits=LIMITS,
        solver_model=SOLVER,
    )


def respond(solution: str = STDIO_REFERENCE, statement: str = STATEMENT):
    def answer(messages, schema):
        reply = code_solver_reply(messages, schema, solution=solution)
        if reply is not None:
            return reply
        return json.dumps({"title": "Касса", "statement": statement}, ensure_ascii=False)

    return answer


def prompt_of(call) -> str:
    return "\n".join(message.content for message in call["messages"])


async def test_stdio_instance_shows_stdin_and_output_examples():
    spec = parse_template(make_stdio(), source="s.md")
    llm = FakeLLM(respond())
    instance = await generate_instance(spec, 7, deps(llm, LocalSandbox()))

    public = instance.public
    assert public.signature.io is IOKind.STDIO
    assert public.signature.entrypoint is None
    assert public.examples and all(e.stdin is not None for e in public.examples)
    assert all(e.input is None for e in public.examples)
    hidden = instance.hidden
    assert hidden.io is IOKind.STDIO and hidden.check.mode == "tokens"
    assert hidden.entrypoint is None
    prompt = prompt_of(llm.calls[0])
    assert "stdin" in prompt and "программ" in prompt
    # Публичный пример виден писателю условия, последний скрытый кейс — нет.
    assert hidden.cases[0].stdin in prompt
    assert len(public.examples) < len(hidden.cases)
    assert hidden.cases[-1].expected.strip() not in prompt


async def test_stdio_solver_writes_a_program_and_runs_it_on_all_cases():
    spec = parse_template(make_stdio(), source="s.md")
    sandbox = LocalSandbox()
    llm = FakeLLM(respond())
    instance = await generate_instance(spec, 7, deps(llm, sandbox, solver_enabled=True))

    solver_prompt = prompt_of(llm.calls[1])
    assert "stdin" in solver_prompt and "функци" not in solver_prompt.split("\n")[0]
    solver_run = sandbox.test_runs[-1]
    assert solver_run.io is IOKind.STDIO
    assert solver_run.cases == instance.hidden.cases
    assert instance.hidden.solver.passed


async def test_stdio_statement_with_hidden_output_is_rejected():
    spec = parse_template(make_stdio(), source="s.md")
    # Последний скрытый кейс ждёт вывод из шести строк: их появление в условии — утечка.
    leaked = STATEMENT + " Например, ответ: 3\n0\n5\n10\n0\n21"
    llm = FakeLLM(respond(statement=leaked))
    with pytest.raises(ModelOutputError, match="скрытых"):
        await generate_instance(spec, 7, deps(llm, LocalSandbox(), legend_attempts=1))
