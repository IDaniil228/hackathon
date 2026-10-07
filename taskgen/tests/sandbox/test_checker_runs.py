"""Свой проверяльщик шаблона: исполняется в песочнице, вердикт — его код выхода.

Поддельный Piston отличает запуск решения от запуска проверяльщика по аргументам: так
видно, что проверяльщик ушёл в песочницу, а не исполнился в сервисе.
"""

import json

import pytest

from taskgen.contracts import (
    Checker,
    IOKind,
    OutputCheck,
    RunLimits,
    TestCase,
    TestRunRequest,
)
from taskgen.sandbox.cases import CHECKER_ARGS, CHECKER_FAILURE
from taskgen.sandbox.errors import SandboxError
from tests.sandbox.fakes import execute_response, fake_runner, stage

LIMITS = RunLimits(timeout_ms=2000, memory_bytes=128 * 1024 * 1024)
CHECKER = Checker(runtime="python", code="import sys\n# проверяльщик шаблона\n")


def by_files(body: dict) -> dict[str, str]:
    return {item["name"]: item["content"] for item in body["files"]}


def piston(program_output: str, verdict, sent: list[dict]):
    """Решение печатает program_output, проверяльщик отвечает кодом verdict(файлы)."""

    def answer(body: dict):
        sent.append(body)
        if body["args"] == CHECKER_ARGS:
            return execute_response(stage(code=verdict(by_files(body)), stdout="comment"))
        return execute_response(stage(stdout=program_output))

    return fake_runner(answer)


def stdio_request(cases, check):
    return TestRunRequest(
        runtime="python",
        io=IOKind.STDIO,
        solution="print(input())",
        cases=cases,
        check=check,
        limits=LIMITS,
    )


async def test_checker_runs_in_sandbox_with_input_output_and_answer():
    sent: list[dict] = []
    runner = piston(
        "4 1\n", lambda files: 0 if set(files["output.txt"].split()) == {"1", "4"} else 1, sent
    )
    case = TestCase(stdin="1 4\n", expected="1 4\n")

    report = await runner.run_tests(
        stdio_request([case], OutputCheck(mode="checker", checker=CHECKER))
    )

    assert report.outcomes[0].passed
    checker_runs = [body for body in sent if body["args"] == CHECKER_ARGS]
    assert len(checker_runs) == 1
    files = by_files(checker_runs[0])
    assert list(files)[0] == "solution.py"
    assert files["solution.py"] == CHECKER.code
    assert files["input.txt"] == "1 4\n"
    assert files["output.txt"] == "4 1\n"
    assert files["answer.txt"] == "1 4\n"
    # Решение кандидата ожидаемого ответа не получает.
    solution_runs = [body for body in sent if body["args"] != CHECKER_ARGS]
    assert all("answer.txt" not in by_files(body) for body in solution_runs)


async def test_checker_exit_one_is_wrong_answer():
    sent: list[dict] = []
    runner = piston("5\n", lambda files: 1, sent)

    report = await runner.run_tests(
        stdio_request(
            [TestCase(stdin="", expected="4")], OutputCheck(mode="checker", checker=CHECKER)
        )
    )

    assert not report.outcomes[0].passed
    assert report.outcomes[0].error == "неверный ответ"


async def test_checker_crash_is_named_and_fails_the_case():
    sent: list[dict] = []
    runner = piston("5\n", lambda files: 2, sent)

    report = await runner.run_tests(
        stdio_request(
            [TestCase(stdin="", expected="4")], OutputCheck(mode="checker", checker=CHECKER)
        )
    )

    assert not report.outcomes[0].passed
    assert report.outcomes[0].error.startswith(CHECKER_FAILURE)


async def test_failed_solution_run_is_not_sent_to_checker():
    sent: list[dict] = []

    def answer(body: dict):
        sent.append(body)
        return execute_response(stage(code=1, status="RE", stderr="boom"))

    runner = fake_runner(answer)

    report = await runner.run_tests(
        stdio_request(
            [TestCase(stdin="", expected="4")], OutputCheck(mode="checker", checker=CHECKER)
        )
    )

    assert not report.outcomes[0].passed
    assert all(body["args"] != CHECKER_ARGS for body in sent)


async def test_function_answers_reach_checker_as_json():
    sent: list[dict] = []

    def answer(body: dict):
        sent.append(body)
        if body["args"] == CHECKER_ARGS:
            return execute_response(stage(code=0))
        nonce = body["stdin"].split("\n", 1)[0]
        record = {"case": 0, "ok": True, "value": [2, 1], "ms": 1}
        return execute_response(stage(stdout=f"{nonce} {json.dumps(record)}\n"))

    runner = fake_runner(answer)
    request = TestRunRequest(
        runtime="python",
        io=IOKind.FUNCTION,
        entrypoint="f",
        solution="def f(a, b):\n    return [b, a]\n",
        cases=[TestCase(input=[1, 2], expected=[1, 2])],
        check=OutputCheck(mode="checker", checker=CHECKER),
        limits=LIMITS,
    )

    report = await runner.run_tests(request)

    assert report.outcomes[0].passed
    files = by_files(next(body for body in sent if body["args"] == CHECKER_ARGS))
    assert json.loads(files["input.txt"]) == [1, 2]
    assert json.loads(files["output.txt"]) == [2, 1]
    assert json.loads(files["answer.txt"]) == [1, 2]


async def test_checker_mode_without_program_is_a_template_error():
    runner = fake_runner(lambda body: execute_response(stage(stdout="1")))

    with pytest.raises(SandboxError, match="проверяльщик"):
        await runner.run_tests(
            stdio_request([TestCase(stdin="", expected="1")], OutputCheck(mode="checker"))
        )


async def test_oversized_output_is_not_sent_to_checker():
    sent: list[dict] = []
    runner = piston("x" * 200_000, lambda files: 0, sent)

    report = await runner.run_tests(
        stdio_request(
            [TestCase(stdin="", expected="x")], OutputCheck(mode="checker", checker=CHECKER)
        )
    )

    assert not report.outcomes[0].passed
    assert "байт" in report.outcomes[0].error
    assert all(body["args"] != CHECKER_ARGS for body in sent)


async def test_checker_runtime_must_accept_data_files():
    sent: list[dict] = []
    runner = piston("1", lambda files: 0, sent)
    cpp = Checker(runtime="cpp", code="int main() { return 0; }")

    with pytest.raises(SandboxError, match="cpp"):
        await runner.run_tests(
            stdio_request(
                [TestCase(stdin="", expected="1")], OutputCheck(mode="checker", checker=cpp)
            )
        )
    assert sent == []
