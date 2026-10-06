"""Маппинг RunRequest в запрос Piston и разбор ответа, без реального Piston."""

import httpx
import pytest

from taskgen.config import SandboxSettings
from taskgen.contracts import RunLimits, RunRequest
from taskgen.sandbox.errors import SandboxError
from taskgen.sandbox.piston import PistonRunner
from tests.sandbox.fakes import execute_response as ok
from tests.sandbox.fakes import fake_runner, stage

LIMITS = RunLimits(timeout_ms=1500, memory_bytes=128 * 1024 * 1024)


def runner_answering(response: httpx.Response, sent: list[dict] | None = None) -> PistonRunner:
    return fake_runner(lambda body: response, sent)


async def test_request_maps_runtime_files_and_limits():
    sent: list[dict] = []
    runner = runner_answering(ok(stage()), sent)

    await runner.run(
        RunRequest(
            runtime="python",
            files={"main.py": "import util", "util.py": "x = 1"},
            stdin="5\n",
            args=["--fast"],
            limits=LIMITS,
        )
    )

    body = sent[0]
    assert (body["language"], body["version"]) == ("python", "3.12.0")
    # Piston запускает первый файл, поэтому порядок файлов сохраняется.
    assert [f["name"] for f in body["files"]] == ["main.py", "util.py"]
    assert body["files"][1]["content"] == "x = 1"
    assert body["stdin"] == "5\n"
    assert body["args"] == ["--fast"]
    assert body["run_timeout"] == 1500
    assert body["run_cpu_time"] == 1500
    assert body["run_memory_limit"] == 128 * 1024 * 1024


async def test_runtime_minimum_limits_raise_small_requests_only():
    sent: list[dict] = []
    runner = runner_answering(ok(stage()), sent)
    small = RunLimits(timeout_ms=1000, memory_bytes=64 * 1024 * 1024)
    large = RunLimits(timeout_ms=50_000, memory_bytes=4 * 1024 * 1024 * 1024)

    await runner.run(RunRequest(runtime="python-ml", files={"m.py": ""}, limits=small))
    await runner.run(RunRequest(runtime="python-ml", files={"m.py": ""}, limits=large))
    await runner.run(RunRequest(runtime="python", files={"m.py": ""}, limits=small))

    ml_floor, ml_large, plain = sent
    # Один импорт torch не помещается в лимиты по умолчанию, поэтому у окружения есть пол.
    assert ml_floor["run_memory_limit"] > small.memory_bytes
    assert ml_floor["run_timeout"] > small.timeout_ms
    assert ml_floor["run_cpu_time"] == ml_floor["run_timeout"]
    assert ml_large["run_memory_limit"] == large.memory_bytes
    assert ml_large["run_timeout"] == large.timeout_ms
    assert plain["run_memory_limit"] == small.memory_bytes
    assert plain["run_timeout"] == small.timeout_ms


async def test_compiled_runtime_limits_compiler_memory():
    sent: list[dict] = []
    runner = runner_answering(ok(stage(), compile_stage=stage()), sent)

    await runner.run(RunRequest(runtime="cpp", files={"main.cpp": ""}, limits=LIMITS))

    assert sent[0]["language"] == "c++"
    assert sent[0]["compile_memory_limit"] > 0


async def test_successful_run_is_parsed():
    runner = runner_answering(ok(stage(stdout="hi\n", stderr="warn\n", wall_time=18)))

    result = await runner.run(RunRequest(runtime="python", files={"m.py": ""}, limits=LIMITS))

    assert result.stdout == "hi\n"
    assert result.stderr == "warn\n"
    assert result.exit_code == 0
    assert result.signal is None
    assert not result.timed_out
    assert result.wall_ms == 18
    assert result.memory_bytes == 3_784_000


async def test_time_limit_is_reported_as_timeout():
    runner = runner_answering(
        ok(
            stage(
                signal="SIGKILL",
                code=None,
                status="TO",
                message="Time limit exceeded (wall clock)",
                wall_time=2100,
            )
        )
    )

    result = await runner.run(RunRequest(runtime="python", files={"m.py": ""}, limits=LIMITS))

    assert result.timed_out
    assert result.signal == "SIGKILL"
    assert result.exit_code is None


async def test_signal_seen_through_bash_wrapper_is_recovered():
    # Пакеты Piston запускают код через bash-скрипт run, и смерть процесса от сигнала
    # доходит до Piston только кодом выхода 128 + номер сигнала.
    runner = runner_answering(
        ok(stage(code=137, status="RE", message="Exited with error status 137"))
    )

    result = await runner.run(RunRequest(runtime="python", files={"m.py": ""}, limits=LIMITS))

    assert result.signal == "SIGKILL"
    assert result.exit_code == 137
    assert not result.timed_out


async def test_ordinary_error_exit_has_no_signal():
    runner = runner_answering(ok(stage(code=3, status="RE")))

    result = await runner.run(RunRequest(runtime="python", files={"m.py": ""}, limits=LIMITS))

    assert (result.exit_code, result.signal) == (3, None)


async def test_compile_failure_returns_compiler_output():
    failed = stage(code=1, stderr="main.cpp:1: error: expected ';'\n", status="RE")
    runner = runner_answering(ok(failed, compile_stage=failed))

    result = await runner.run(RunRequest(runtime="cpp", files={"main.cpp": "x"}, limits=LIMITS))

    assert result.exit_code == 1
    assert "expected ';'" in result.stderr


async def test_piston_refusal_is_a_sandbox_error():
    runner = runner_answering(
        httpx.Response(400, json={"message": "run_timeout cannot exceed the configured limit"})
    )

    with pytest.raises(SandboxError, match="run_timeout cannot exceed"):
        await runner.run(RunRequest(runtime="python", files={"m.py": ""}, limits=LIMITS))


async def test_unknown_runtime_fails_before_any_request():
    sent: list[dict] = []
    runner = runner_answering(ok(stage()), sent)

    with pytest.raises(SandboxError, match="brainfuck"):
        await runner.run(RunRequest(runtime="brainfuck", files={"m.b": ""}, limits=LIMITS))
    assert sent == []


async def test_unreachable_piston_is_a_sandbox_error():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    http = httpx.AsyncClient(base_url="http://piston.test", transport=httpx.MockTransport(refuse))
    runner = PistonRunner(SandboxSettings(piston_url="http://piston.test"), http=http)

    with pytest.raises(SandboxError, match="недоступен"):
        await runner.run(RunRequest(runtime="python", files={"m.py": ""}, limits=LIMITS))
