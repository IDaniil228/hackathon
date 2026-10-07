"""Прогон задачи stdio: программа кандидата запускается на каждом кейсе отдельно.

Поддельный Piston отвечает по телу запроса: так видно, что именно уходит в песочницу.
Настоящие запуски проверяет test_stdio_live.py.
"""

import asyncio
import json

import httpx
import pytest

from taskgen.config import SandboxSettings
from taskgen.contracts import IOKind, OutputCheck, RunLimits, TestCase, TestRunRequest
from taskgen.sandbox.errors import SandboxError
from taskgen.sandbox.piston import PistonRunner
from taskgen.sandbox.registry import RuntimeRegistry
from tests.sandbox.fakes import execute_response, fake_runner, stage

LIMITS = RunLimits(timeout_ms=2000, memory_bytes=128 * 1024 * 1024)
SECRET = "918273645"
PROGRAM = "import sys\nprint(sum(map(int, sys.stdin.read().split())))\n"


def request(cases=None, *, mode="tokens", runtime="python", solution=PROGRAM):
    return TestRunRequest(
        runtime=runtime,
        io=IOKind.STDIO,
        solution=solution,
        cases=cases
        or [
            TestCase(stdin="1 2\n", expected="3\n"),
            TestCase(stdin="5 5\n", expected=SECRET + "\n"),
        ],
        check=OutputCheck(mode=mode),
        limits=LIMITS,
    )


def summing(body: dict) -> httpx.Response:
    """Piston, который исполняет программу-сумматор: печатает сумму чисел из stdin."""
    total = sum(int(token) for token in body["stdin"].split())
    return execute_response(stage(stdout=f"{total}\n"))


async def test_each_case_is_its_own_run_with_case_stdin():
    sent: list[dict] = []
    runner = fake_runner(summing, sent)

    report = await runner.run_tests(request())

    assert sorted(body["stdin"] for body in sent) == ["1 2\n", "5 5\n"]
    for body in sent:
        # Исполняется первый файл — программа кандидата, ожидаемый вывод в песочницу не уходит.
        assert body["files"][0] == {"name": "solution.py", "content": PROGRAM}
        assert SECRET not in json.dumps(body)
    assert [o.passed for o in report.outcomes] == [True, False]
    assert report.outcomes[1].error == "неверный ответ"


async def test_case_files_lie_next_to_the_program():
    sent: list[dict] = []
    runner = fake_runner(summing, sent)
    case = TestCase(stdin="1\n", files={"access.log": "GET / 200\n"}, expected="1")

    await runner.run_tests(request([case]))

    assert sent[0]["files"][1:] == [{"name": "access.log", "content": "GET / 200\n"}]


async def test_time_memory_and_wall_come_from_each_run():
    walls = {"1\n": 15, "2\n": 40}
    runner = fake_runner(
        lambda body: execute_response(
            stage(stdout=body["stdin"], wall_time=walls[body["stdin"]], memory=walls[body["stdin"]])
        )
    )
    cases = [TestCase(stdin="1\n", expected="1"), TestCase(stdin="2\n", expected="2")]

    report = await runner.run_tests(request(cases))

    assert [o.wall_ms for o in report.outcomes] == [15, 40]
    assert report.max_wall_ms == 40
    assert report.max_memory_bytes == 40


async def test_nonzero_exit_fails_the_case_with_code_and_stderr():
    runner = fake_runner(
        lambda body: execute_response(
            stage(stdout="3\n", stderr="ValueError: bad line\n", code=1, status="RE")
        )
    )

    report = await runner.run_tests(request([TestCase(stdin="1 2", expected="3")]))

    error = report.outcomes[0].error
    assert not report.outcomes[0].passed
    assert "код" in error and "1" in error and "ValueError" in error


async def test_limits_are_named():
    answers = {
        "t": stage(code=None, signal="SIGKILL", status="TO", wall_time=2100),
        "m": stage(code=137, status="RE", memory=LIMITS.memory_bytes - 1000),
        "o": stage(code=None, signal="SIGKILL", status="OL"),
    }
    runner = fake_runner(lambda body: execute_response(answers[body["stdin"]]))
    cases = [TestCase(stdin=key, expected="x") for key in answers]

    report = await runner.run_tests(request(cases))

    errors = [o.error for o in report.outcomes]
    assert "врем" in errors[0]
    assert "памят" in errors[1]
    assert "вывод" in errors[2]


async def test_compile_error_stops_after_first_case():
    sent: list[dict] = []
    failed = stage(code=1, stderr="solution.cpp:1:1: error: 'x' does not name a type\n")
    runner = fake_runner(lambda body: execute_response(failed, compile_stage=failed), sent)
    cases = [TestCase(stdin=str(i), expected=str(i)) for i in range(5)]

    report = await runner.run_tests(request(cases, runtime="cpp", solution="x"))

    assert "does not name a type" in report.compile_error
    assert len(report.outcomes) == 5 and not any(o.passed for o in report.outcomes)
    assert len(sent) == 1


async def test_check_mode_comes_from_the_request():
    runner = fake_runner(lambda body: execute_response(stage(stdout="3")))
    case = TestCase(stdin="1 2", expected="3\n")

    tokens = await runner.run_tests(request([case], mode="tokens"))
    exact = await runner.run_tests(request([case], mode="exact"))

    assert tokens.outcomes[0].passed
    assert not exact.outcomes[0].passed


async def test_case_runs_are_bounded_by_concurrency_setting():
    active = peak = 0

    async def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        body = json.loads(http_request.content)
        return execute_response(stage(stdout=body["stdin"]))

    http = httpx.AsyncClient(base_url="http://piston.test", transport=httpx.MockTransport(handler))
    settings = SandboxSettings(piston_url="http://piston.test", stdio_concurrency=3)
    runner = PistonRunner(settings, http=http)
    cases = [TestCase(stdin=str(i), expected=str(i)) for i in range(12)]

    report = await runner.run_tests(request(cases))

    assert all(o.passed for o in report.outcomes)
    assert 1 < peak <= 3


async def test_case_files_need_a_runtime_that_keeps_data_files():
    # Скрипт compile пакета gcc собирает каждый файл запуска как исходник C++.
    runner = fake_runner(summing)
    case = TestCase(stdin="", files={"data.txt": "1"}, expected="1")

    with pytest.raises(SandboxError, match="файл"):
        await runner.run_tests(request([case], runtime="cpp", solution="int main(){}"))


async def test_case_file_cannot_replace_the_program():
    runner = fake_runner(summing)
    case = TestCase(stdin="", files={"solution.py": "print(1)"}, expected="1")

    with pytest.raises(SandboxError, match="solution.py"):
        await runner.run_tests(request([case]))


def test_every_runtime_names_its_program_file():
    registry = RuntimeRegistry.load()
    for name in registry.names():
        assert registry.get(name).source_file


async def test_service_tool_runtime_does_not_run_solutions():
    sent: list[dict] = []
    runner = fake_runner(summing, sent)

    with pytest.raises(SandboxError, match="служебное"):
        await runner.run_tests(request(runtime="analyzers"))
    assert sent == []
