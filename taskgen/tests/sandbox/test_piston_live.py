"""Запуски против реального Piston с установленными пакетами python и gcc."""

import pytest

from taskgen.contracts import RunLimits, RunRequest
from taskgen.sandbox.registry import RuntimeRegistry

pytestmark = pytest.mark.integration

LIMITS = RunLimits(timeout_ms=3000, memory_bytes=256 * 1024 * 1024)


async def test_python_runs_with_stdin_and_args(piston):
    result = await piston.run(
        RunRequest(
            runtime="python",
            files={"main.py": "import sys\nprint(sys.argv[1], input()[::-1])"},
            stdin="abc",
            args=["arg"],
            limits=LIMITS,
        )
    )

    assert (result.exit_code, result.stdout) == (0, "arg cba\n"), result.stderr


async def test_cpp_compiles_and_runs(piston):
    source = "#include <iostream>\nint main() { int x; std::cin >> x; std::cout << x * 2; }\n"

    result = await piston.run(
        RunRequest(runtime="cpp", files={"main.cpp": source}, stdin="21", limits=LIMITS)
    )

    assert (result.exit_code, result.stdout) == (0, "42"), result.stderr


async def test_cpp_standard_in_registry_is_what_piston_compiles_with(piston):
    # Стандарт в описании окружения для кандидата должен совпадать с флагом компилятора.
    standard = RuntimeRegistry.load().get("cpp").standard
    year = standard.upper().removeprefix("C++")
    source = "#include <iostream>\nint main() { std::cout << __cplusplus; }\n"

    result = await piston.run(RunRequest(runtime="cpp", files={"main.cpp": source}, limits=LIMITS))

    assert result.exit_code == 0, result.stderr
    assert result.stdout.startswith(f"20{year}"), (standard, result.stdout)


async def test_cpp_compile_error_reaches_caller(piston):
    result = await piston.run(
        RunRequest(runtime="cpp", files={"main.cpp": "int main() { return x; }"}, limits=LIMITS)
    )

    assert result.exit_code != 0
    assert "x" in result.stderr and "error" in result.stderr
