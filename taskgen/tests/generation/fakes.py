"""Подделки песочницы для тестов генерации.

LocalSandbox исполняет код в локальном процессе Python: тесты проверяют настоящую обвязку
генератора и настоящий эталон, а не заранее заготовленные ответы. Изоляции здесь нет, поэтому
подделка годится только для шаблонов, которые написаны в самих тестах.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from taskgen.contracts import CaseOutcome, RunRequest, RunResult, TestReport, TestRunRequest


def connect_error() -> httpx.ConnectError:
    return httpx.ConnectError("песочница недоступна", request=httpx.Request("POST", "http://pb"))


def call_entrypoint(solution: str, entrypoint: str, value: Any) -> Any:
    namespace: dict[str, Any] = {}
    exec(compile(solution, "solution.py", "exec"), namespace)
    # Контракт песочницы: input — массив позиционных аргументов или объект именованных.
    if isinstance(value, dict):
        return namespace[entrypoint](**value)
    return namespace[entrypoint](*value)


class LocalSandbox:
    """SandboxRunner на локальном Python; запоминает запросы и умеет имитировать сбой."""

    def __init__(self) -> None:
        self.runs: list[RunRequest] = []
        self.test_runs: list[TestRunRequest] = []
        self.down = False
        # Подмена отчёта эталона: функция получает запрос и честный отчёт, возвращает итоговый.
        self.tamper: Callable[[TestRunRequest, TestReport], TestReport] | None = None

    async def run(self, request: RunRequest) -> RunResult:
        self.runs.append(request)
        if self.down:
            raise connect_error()
        assert request.runtime == "python"
        with tempfile.TemporaryDirectory() as directory:
            # Как в Piston: исполняется первый файл, остальные лежат рядом.
            paths = []
            for name, content in request.files.items():
                path = Path(directory) / name
                path.write_text(content, encoding="utf-8")
                paths.append(path)
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                str(paths[0]),
                *request.args,
                cwd=directory,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(request.stdin.encode()),
                    timeout=request.limits.timeout_ms / 1000,
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                return RunResult(stdout="", stderr="", exit_code=None, timed_out=True)
        return RunResult(
            stdout=stdout.decode(), stderr=stderr.decode(), exit_code=process.returncode
        )

    async def run_tests(self, request: TestRunRequest) -> TestReport:
        self.test_runs.append(request)
        if self.down:
            raise connect_error()
        outcomes = []
        for case in request.cases:
            try:
                passed = call_entrypoint(request.solution, request.entrypoint, case.input) == (
                    case.expected
                )
                outcomes.append(CaseOutcome(passed=passed, wall_ms=1))
            except Exception as error:
                outcomes.append(CaseOutcome(passed=False, wall_ms=1, error=repr(error)))
        report = TestReport(outcomes=outcomes, max_wall_ms=1, max_memory_bytes=1024)
        if self.tamper is not None:
            report = self.tamper(request, report)
        return report
