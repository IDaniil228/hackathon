"""Прогон решения на тестовых кейсах поверх ядра песочницы.

Ядро — запуск программы (SandboxRunner.run): stdin и файлы на вход; stdout, stderr, код
выхода, время, память и ошибка компиляции на выход. Над ним два вида задач:

- io=stdio — программа кандидата запускается на каждом кейсе отдельным запуском: stdin
  кейса — её stdin, файлы кейса лежат рядом, stdout — ответ. Время и память меряются на
  каждом кейсе. Весь stdout принадлежит программе, служебных строк в нём нет, поэтому метка
  запуска не нужна. Первый кейс идёт один: если программа не собралась, остальные не
  запускаются;
- io=function — адаптер: драйвер языка (taskgen/sandbox/drivers) вызывает функцию
  кандидата на входах всех кейсов одним запуском и печатает ответы JSON по протоколу
  protocol.py.

Ответ кейса сверяется с ожидаемым режимом OutputCheck: встроенные режимы — checkers.py.
"""

from __future__ import annotations

import asyncio
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


async def run_tests(
    run: Run, registry: RuntimeRegistry, request: TestRunRequest, *, concurrency: int
) -> TestReport:
    """Исполняет решение на кейсах и сверяет ответы режимом request.check.

    concurrency — сколько запусков кейсов stdio идёт одновременно.
    """
    spec = registry.get(request.runtime)
    if spec.kind != "language":
        raise SandboxError(f"окружение {request.runtime} служебное, решения в нём не запускаются")
    checker = _checker_spec(registry, request)
    if request.io is IOKind.FUNCTION:
        execution = await _function(run, spec, request)
    else:
        execution = await _stdio(run, spec, request, concurrency)
    if execution.compile_error is not None:
        return failed_report(request.cases, execution.compile_error)
    gate = asyncio.Semaphore(concurrency)

    async def verdict(case: TestCase, produced: Produced) -> CaseOutcome:
        async with gate:
            return await _verdict(run, checker, request, case, produced)

    outcomes = list(
        await asyncio.gather(
            *(
                verdict(case, produced)
                for case, produced in zip(request.cases, execution.produced, strict=True)
            )
        )
    )
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


# --- Задача stdio: запуск программы на каждом кейсе ---------------------------------------


async def _stdio(
    run: Run, spec: RuntimeSpec, request: TestRunRequest, concurrency: int
) -> Execution:
    for case in request.cases:
        check_case_files(spec, case)
    if not request.cases:
        return Execution([])
    limits = spec.effective_limits(request.limits)

    def launch(case: TestCase) -> Awaitable[RunResult]:
        files = {spec.source_file: request.solution, **case.files}
        return run(
            RunRequest(
                runtime=request.runtime,
                files=files,
                stdin=case.stdin or "",
                limits=request.limits,
            )
        )

    first = await launch(request.cases[0])
    if first.compile_error is not None:
        return Execution([], compile_error=first.compile_error)
    gate = asyncio.Semaphore(concurrency)

    async def bounded(case: TestCase) -> RunResult:
        async with gate:
            return await launch(case)

    rest = await asyncio.gather(*(bounded(case) for case in request.cases[1:]))
    results = [first, *rest]
    memory = [r.memory_bytes for r in results if r.memory_bytes is not None]
    return Execution(
        [_program_output(result, limits) for result in results],
        max_memory_bytes=max(memory) if memory else None,
    )


def check_case_files(spec: RuntimeSpec, case: TestCase) -> None:
    """Файлы кейса допустимы для окружения: SandboxError — ошибка шаблона, а не решения."""
    if not case.files:
        return
    if not spec.data_files:
        raise SandboxError(
            f"окружение {spec.language} не принимает файлы рядом с программой: его пакет "
            "собирает каждый файл запуска как исходник"
        )
    if spec.source_file in case.files:
        raise SandboxError(f"файл кейса {spec.source_file} заменил бы программу кандидата")


def _program_output(result: RunResult, limits: RunLimits) -> Produced:
    if result.compile_error is not None:
        # Тот же исходник собрался на первом кейсе; повторный отказ — сбой сборки, а не ответ.
        return Produced(error=f"ошибка компиляции: {result.compile_error[-STDERR_TAIL:]}")
    if result.exit_code == 0 and not (result.timed_out or result.output_limited):
        return Produced(output=result.stdout, wall_ms=result.wall_ms)
    if result.signal is not None:
        fallback = f"аварийное завершение по сигналу {result.signal}"
    else:
        fallback = f"программа завершилась с кодом {result.exit_code}"
    # Время кейса без ответа не учитывается, как и у задачи-функции.
    return Produced(error=failure_reason(result, limits, fallback))


# --- Задача-функция: адаптер над ядром ---------------------------------------------------


async def _function(run: Run, spec: RuntimeSpec, request: TestRunRequest) -> Execution:
    if not spec.functions:
        raise SandboxError(
            f"у окружения {request.runtime} нет драйвера задач-функций, только io=stdio"
        )
    driver = spec.function_driver or ""
    files, args = driver_files(driver, request.entrypoint or "", request.solution)
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


def input_text(io: IOKind, case: TestCase) -> str:
    """Вход кейса текстом: у функции это JSON аргументов, у stdio — stdin."""
    if io is IOKind.FUNCTION:
        return _json_text(case.input)
    return case.stdin or ""


def expected_text(io: IOKind, case: TestCase) -> str:
    """Ожидаемый ответ текстом: у функции это JSON значения, у stdio — сама строка."""
    if io is IOKind.FUNCTION:
        return _json_text(case.expected)
    return str(case.expected)


async def _verdict(
    run: Run,
    checker: RuntimeSpec | None,
    request: TestRunRequest,
    case: TestCase,
    produced: Produced,
) -> CaseOutcome:
    if produced.output is None:
        return CaseOutcome(passed=False, wall_ms=produced.wall_ms, error=produced.error)
    if checker is None:
        passed = output_matches(
            request.check.mode, produced.output, expected_text(request.io, case)
        )
        error = None if passed else WRONG_ANSWER
    else:
        error = await _run_checker(run, checker, request, case, produced.output)
        passed = error is None
    return CaseOutcome(passed=passed, wall_ms=produced.wall_ms, error=error)


# --- Свой проверяльщик шаблона -----------------------------------------------------------
#
# Проверяльщик — программа шаблона на любом языке реестра, который принимает файлы данных.
# Она исполняется в песочнице, как решение, а не в сервисе. Рядом с ней лежат input.txt
# (вход кейса: stdin у stdio, JSON аргументов у функции), output.txt (ответ решения),
# answer.txt (ожидаемый ответ) и файлы кейса stdio; имена приходят и аргументами в порядке
# testlib. Код выхода 0 — верно, 1 — неверно, другой код, сигнал или лимит — отказ самого
# проверяльщика: кейс не засчитан, а причина начинается с CHECKER_FAILURE.

CHECKER_FILES = ("input.txt", "output.txt", "answer.txt")
CHECKER_ARGS = list(CHECKER_FILES)
CHECKER_FAILURE = "проверяльщик отказал"
# Piston принимает тело запроса не больше 100 КиБ; ответ решения уходит в нём целиком.
CHECKER_MAX_OUTPUT_BYTES = 64 * 1024


def _checker_spec(registry: RuntimeRegistry, request: TestRunRequest) -> RuntimeSpec | None:
    """Окружение своего проверяльщика; None — режим встроенный. SandboxError — ошибка шаблона."""
    if request.check.mode != "checker":
        return None
    checker = request.check.checker
    if checker is None:
        raise SandboxError("режим checker без программы проверяльщика")
    spec = registry.get(checker.runtime)
    if not spec.data_files:
        raise SandboxError(
            f"проверяльщик на {checker.runtime} не получит файлы ответа: пакет окружения "
            "собирает каждый файл запуска как исходник"
        )
    reserved = {spec.source_file, *CHECKER_FILES}
    for case in request.cases:
        clash = reserved & set(case.files)
        if clash:
            raise SandboxError(f"файл кейса {sorted(clash)[0]} занят проверяльщиком")
    return spec


async def _run_checker(
    run: Run, spec: RuntimeSpec, request: TestRunRequest, case: TestCase, output: str
) -> str | None:
    """Причина отказа или None, если проверяльщик принял ответ."""
    size = len(output.encode())
    if size > CHECKER_MAX_OUTPUT_BYTES:
        return f"ответ занимает {size} байт, проверяльщик принимает до {CHECKER_MAX_OUTPUT_BYTES}"
    checker = request.check.checker
    assert checker is not None
    texts = (input_text(request.io, case), output, expected_text(request.io, case))
    files = {spec.source_file: checker.code, **dict(zip(CHECKER_FILES, texts, strict=True))}
    if request.io is IOKind.STDIO:
        files |= case.files
    result = await run(
        RunRequest(runtime=checker.runtime, files=files, args=CHECKER_ARGS, limits=request.limits)
    )
    if result.compile_error is not None:
        return f"{CHECKER_FAILURE}: не собрался: {result.compile_error[-STDERR_TAIL:]}"
    if result.exit_code == 0 and not result.timed_out:
        return None
    if result.exit_code == 1 and not result.timed_out:
        return WRONG_ANSWER
    limits = spec.effective_limits(request.limits)
    reason = failure_reason(result, limits, f"код выхода {result.exit_code}")
    return f"{CHECKER_FAILURE}: {reason}"
