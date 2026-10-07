"""Прогон решения на тестовых кейсах поверх ядра песочницы.

Ядро — запуск программы (SandboxRunner.run): stdin и файлы на вход; stdout, stderr, код
выхода, время, память и ошибка компиляции на выход. Задача-функция — адаптер над ним:
драйвер языка (taskgen/sandbox/drivers) вызывает функцию кандидата на входах всех кейсов
одним запуском и печатает ответы JSON по протоколу protocol.py.

Ответ кейса сверяется с ожидаемым режимом OutputCheck: встроенные режимы — checkers.py.
"""

from __future__ import annotations

import json
import math
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from taskgen.contracts import (
    CaseOutcome,
    IOKind,
    RunLimits,
    RunRequest,
    RunResult,
    TestCase,
    TestReport,
    TestRunRequest,
)
from taskgen.sandbox import protocol
from taskgen.sandbox.checkers import output_matches
from taskgen.sandbox.drivers import driver_files
from taskgen.sandbox.errors import SandboxError
from taskgen.sandbox.registry import RuntimeRegistry, RuntimeSpec

Run = Callable[[RunRequest], Awaitable[RunResult]]

# Пик памяти, начиная с которого убийство процесса приписывается лимиту памяти. Piston
# переводит байты в килобайты и обратно с округлением, поэтому пик не равен лимиту точно.
MEMORY_KILL_SHARE = 0.9
STDERR_TAIL = 500
WRONG_ANSWER = "неверный ответ"


@dataclass
class Produced:
    """Что решение выдало на кейсе до сверки: ответ текстом или причина, почему его нет."""

    output: str | None = None
    error: str | None = None
    wall_ms: int | None = None


@dataclass
class Execution:
    """Итог исполнения решения на всех кейсах, до сверки ответов."""

    produced: list[Produced]
    max_memory_bytes: int | None = None
    compile_error: str | None = None


async def run_tests(run: Run, registry: RuntimeRegistry, request: TestRunRequest) -> TestReport:
    """Исполняет решение на кейсах и сверяет ответы режимом request.check."""
    spec = registry.get(request.runtime)
    if request.io is IOKind.FUNCTION:
        execution = await _function(run, spec, request)
    else:
        raise SandboxError(f"вид задания {request.io} не поддерживается")
    if execution.compile_error is not None:
        return failed_report(request.cases, execution.compile_error)
    outcomes = [
        _verdict(request, case, produced)
        for case, produced in zip(request.cases, execution.produced, strict=True)
    ]
    times = [o.wall_ms for o in outcomes if o.wall_ms is not None]
    return TestReport(
        outcomes=outcomes,
        max_wall_ms=max(times) if times else None,
        max_memory_bytes=execution.max_memory_bytes,
    )


def failed_report(cases: list[TestCase], compile_error: str) -> TestReport:
    # Исходов столько же, сколько кейсов, чтобы pass_rate и разбор по кейсам не зависели
    # от того, на какой стадии остановился прогон.
    return TestReport(
        outcomes=[CaseOutcome(passed=False, error="ошибка компиляции") for _ in cases],
        compile_error=compile_error,
    )


def failure_reason(result: RunResult, limits: RunLimits, fallback: str) -> str:
    """Почему запуск не дал ответа: лимит песочницы или fallback с хвостом stderr."""
    if result.timed_out:
        return "превышен лимит времени"
    if result.output_limited:
        return "превышен лимит вывода"
    memory = result.memory_bytes or 0
    if result.signal == "SIGKILL" and memory >= MEMORY_KILL_SHARE * limits.memory_bytes:
        return "превышен лимит памяти"
    tail = result.stderr.strip()[-STDERR_TAIL:]
    return fallback + (f": {tail}" if tail else "")


# --- Задача-функция: адаптер над ядром ---------------------------------------------------


async def _function(run: Run, spec: RuntimeSpec, request: TestRunRequest) -> Execution:
    if spec.driver is None:
        raise SandboxError(
            f"у окружения {request.runtime} нет драйвера задач-функций, только io=stdio"
        )
    files, args = driver_files(spec.driver, request.entrypoint or "", request.solution)
    nonce = secrets.token_hex(16)
    # Эвристика причины обрыва сравнивает пик памяти с действующим лимитом, а не запрошенным.
    limits = spec.effective_limits(request.limits)
    result = await run(
        RunRequest(
            runtime=request.runtime,
            files=files,
            stdin=protocol.build_stdin(nonce, request.cases),
            args=args,
            limits=request.limits,
        )
    )
    if result.compile_error is not None:
        return Execution([], compile_error=result.compile_error)
    output = protocol.parse_output(result.stdout, nonce)
    if output.load_error is not None:
        return Execution([], compile_error=output.load_error)
    missing = failure_reason(result, limits, "решение оборвало прогон")
    produced = []
    for index in range(len(request.cases)):
        record = output.records.get(index)
        if index in output.duplicated:
            produced.append(Produced(error="нарушен протокол драйвера"))
        elif record is None:
            produced.append(Produced(error=missing))
        elif not record.get("ok"):
            produced.append(Produced(error=str(record.get("error"))))
        else:
            produced.append(
                Produced(output=_json_text(record.get("value")), wall_ms=_ms(record.get("ms")))
            )
    return Execution(produced, max_memory_bytes=result.memory_bytes)


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _ms(value: Any) -> int | None:
    if not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return max(0, round(value))


# --- Сверка ответа ---------------------------------------------------------------------


def expected_text(io: IOKind, case: TestCase) -> str:
    """Ожидаемый ответ текстом: у функции это JSON значения, у stdio — сама строка."""
    if io is IOKind.FUNCTION:
        return _json_text(case.expected)
    return str(case.expected)


def _verdict(request: TestRunRequest, case: TestCase, produced: Produced) -> CaseOutcome:
    if produced.output is None:
        return CaseOutcome(passed=False, wall_ms=produced.wall_ms, error=produced.error)
    if request.check.mode == "checker":
        raise SandboxError("свой проверяльщик пока не поддерживается")
    passed = output_matches(request.check.mode, produced.output, expected_text(request.io, case))
    return CaseOutcome(
        passed=passed, wall_ms=produced.wall_ms, error=None if passed else WRONG_ANSWER
    )
