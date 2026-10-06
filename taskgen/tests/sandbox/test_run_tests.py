"""Прогон тестов: что уходит в Piston и как протокол обвязки превращается в TestReport.

Здесь обвязка не исполняется: поддельный Piston сам печатает записи протокола. Поведение
настоящей обвязки проверяют интеграционные тесты в test_harness_live.py.
"""

import json

import pytest

from taskgen.contracts import RunLimits, TestCase, TestRunRequest
from tests.sandbox.fakes import execute_response, fake_runner, stage

LIMITS = RunLimits(timeout_ms=2000, memory_bytes=128 * 1024 * 1024)
SECRET = 918273645


def request(runtime="python", cases=None, solution="def f(a, b):\n    return a + b\n"):
    return TestRunRequest(
        runtime=runtime,
        entrypoint="f",
        solution=solution,
        cases=cases
        or [
            TestCase(input=[1, 2], expected=3),
            TestCase(input=[0.1, 0.2], expected=0.3),
            TestCase(input=[5, 5], expected=SECRET),
        ],
        limits=LIMITS,
    )


def nonce_of(body: dict) -> str:
    return body["stdin"].split("\n", 1)[0]


def protocol(body: dict, *records: dict, extra: str = "") -> str:
    nonce = nonce_of(body)
    lines = [f"{nonce} {json.dumps(record)}" for record in records]
    return extra + "".join(line + "\n" for line in lines)


def answering(make_run):
    """Ответ Piston строится от тела запроса, потому что обвязка подписывает записи nonce."""
    return fake_runner(lambda body: execute_response(make_run(body)))


async def test_request_carries_inputs_but_never_expected_values():
    sent: list[dict] = []
    runner = fake_runner(lambda body: execute_response(stage()), sent)

    await runner.run_tests(request())

    body = sent[0]
    stdin = body["stdin"]
    assert SECRET not in json.loads(stdin.split("\n")[1])
    assert str(SECRET) not in json.dumps(body)
    assert json.loads(stdin.split("\n")[1]) == [[1, 2], [0.1, 0.2], [5, 5]]
    contents = [f["content"] for f in body["files"]]
    assert any("return a + b" in content for content in contents)
    assert body["run_timeout"] == LIMITS.timeout_ms


async def test_nonce_differs_between_runs():
    sent: list[dict] = []
    runner = fake_runner(lambda body: execute_response(stage()), sent)

    await runner.run_tests(request())
    await runner.run_tests(request())

    assert nonce_of(sent[0]) != nonce_of(sent[1])
    assert len(nonce_of(sent[0])) >= 16


async def test_outcomes_follow_comparison_with_expected():
    runner = answering(
        lambda body: stage(
            stdout=protocol(
                body,
                {"case": 0, "ok": True, "value": 3, "ms": 1.4},
                {"case": 1, "ok": True, "value": 0.30000000000000004, "ms": 0.2},
                {"case": 2, "ok": True, "value": 10, "ms": 7.6},
            ),
            memory=9_000_000,
        )
    )

    report = await runner.run_tests(request())

    assert [o.passed for o in report.outcomes] == [True, True, False]
    assert report.outcomes[2].error is not None
    assert report.outcomes[0].wall_ms == 1
    assert report.max_wall_ms == 8
    assert report.max_memory_bytes == 9_000_000
    assert report.compile_error is None


async def test_exception_in_one_case_is_that_case_error():
    runner = answering(
        lambda body: stage(
            stdout=protocol(
                body,
                {"case": 0, "ok": True, "value": 3, "ms": 1},
                {"case": 1, "ok": False, "error": "ZeroDivisionError: division by zero"},
                {"case": 2, "ok": True, "value": SECRET, "ms": 1},
            )
        )
    )

    report = await runner.run_tests(request())

    assert [o.passed for o in report.outcomes] == [True, False, True]
    assert "ZeroDivisionError" in report.outcomes[1].error


async def test_unsigned_lines_are_ignored():
    # Строка без nonce могла прийти только не от обвязки, например от кода кандидата.
    forged = json.dumps({"case": 2, "ok": True, "value": SECRET, "ms": 0})
    runner = answering(
        lambda body: stage(
            stdout=protocol(body, {"case": 0, "ok": True, "value": 3, "ms": 1}, extra=forged + "\n")
        )
    )

    report = await runner.run_tests(request())

    assert [o.passed for o in report.outcomes] == [True, False, False]


async def test_duplicate_record_fails_the_case():
    runner = answering(
        lambda body: stage(
            stdout=protocol(
                body,
                {"case": 0, "ok": True, "value": 3, "ms": 1},
                {"case": 0, "ok": True, "value": 3, "ms": 1},
            )
        )
    )

    report = await runner.run_tests(request())

    assert not report.outcomes[0].passed


async def test_cases_after_timeout_fail_with_time_limit():
    runner = answering(
        lambda body: stage(
            stdout=protocol(body, {"case": 0, "ok": True, "value": 3, "ms": 1}),
            signal="SIGKILL",
            code=None,
            status="TO",
            wall_time=2100,
        )
    )

    report = await runner.run_tests(request())

    assert [o.passed for o in report.outcomes] == [True, False, False]
    assert "врем" in report.outcomes[1].error
    assert report.outcomes[1].error == report.outcomes[2].error


async def test_memory_kill_is_named_as_memory_limit():
    runner = answering(
        lambda body: stage(
            stdout=protocol(body, {"case": 0, "ok": True, "value": 3, "ms": 1}),
            code=137,
            status="RE",
            memory=LIMITS.memory_bytes - 4000,
        )
    )

    report = await runner.run_tests(request())

    assert "памят" in report.outcomes[1].error


async def test_load_error_is_compile_error():
    runner = answering(
        lambda body: stage(
            stdout=protocol(body, {"load_error": "SyntaxError: invalid syntax (line 1)"})
        )
    )

    report = await runner.run_tests(request(solution="def f(:"))

    assert "SyntaxError" in report.compile_error
    assert len(report.outcomes) == 3
    assert report.pass_rate == 0.0


async def test_compile_stage_failure_is_compile_error():
    failed = stage(code=1, stderr="solution.cpp:1:1: error: 'x' does not name a type\n")
    runner = fake_runner(lambda body: execute_response(failed, compile_stage=failed))

    report = await runner.run_tests(request(runtime="cpp", solution="x"))

    assert "does not name a type" in report.compile_error
    assert not any(o.passed for o in report.outcomes)


async def test_cpp_entrypoint_must_be_an_identifier():
    from taskgen.sandbox.errors import SandboxError

    runner = fake_runner(lambda body: execute_response(stage()))
    bad = request(runtime="cpp").model_copy(update={"entrypoint": 'f); system("rm'})

    with pytest.raises(SandboxError, match="entrypoint"):
        await runner.run_tests(bad)
