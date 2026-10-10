"""Клиент Piston API v2, реализует протокол SandboxRunner.

Piston изолирует каждый запуск через Isolate: отдельный каталог, cgroup с лимитом памяти,
лимит процессов, без сети. Клиент только переводит контракт сервиса в запрос /api/v2/execute
и разбирает ответ.
"""

from __future__ import annotations

import signal as signals
from typing import Any

import httpx

from taskgen.config import SandboxSettings
from taskgen.contracts import RunLimits, RunRequest, RunResult, TestReport, TestRunRequest
from taskgen.sandbox import cases
from taskgen.sandbox.errors import SandboxServiceError
from taskgen.sandbox.registry import RuntimeRegistry, RuntimeSpec

# Запас к лимитам компиляции и запуска на очередь задач Piston.
HTTP_MARGIN_SECONDS = 30.0


class PistonRunner:
    def __init__(
        self,
        settings: SandboxSettings,
        *,
        http: httpx.AsyncClient | None = None,
        registry: RuntimeRegistry | None = None,
    ) -> None:
        self._http = http or httpx.AsyncClient(base_url=settings.piston_url.rstrip("/"))
        self._registry = registry or RuntimeRegistry.load()
        self._stdio_concurrency = settings.stdio_concurrency
        self._compile_timeout_ms = settings.compile_timeout_ms

    async def run(self, request: RunRequest) -> RunResult:
        spec = self._registry.get(request.runtime)
        response = await self._execute(
            spec,
            [{"name": name, "content": content} for name, content in request.files.items()],
            stdin=request.stdin,
            args=request.args,
            limits=spec.effective_limits(request.limits),
        )
        result = stage_result(response["run"])
        compiled = response.get("compile")
        if compiled is not None and compiled.get("code") != 0:
            result.compile_error = _compile_error(compiled)
        return result

    async def run_tests(self, request: TestRunRequest) -> TestReport:
        """Прогоняет решение на кейсах поверх run; устройство описано в cases.py."""
        return await cases.run_tests(
            self.run, self._registry, request, concurrency=self._stdio_concurrency
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _execute(
        self,
        spec: RuntimeSpec,
        files: list[dict[str, str]],
        *,
        stdin: str,
        args: list[str],
        limits: RunLimits,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "language": spec.language,
            "version": spec.version,
            "files": files,
            "stdin": stdin,
            "args": args,
            "run_timeout": limits.timeout_ms,
            # Без лимита процессорного времени Piston подставит свой, и он может оказаться
            # меньше запрошенного лимита по стенным часам.
            "run_cpu_time": limits.timeout_ms,
            "run_memory_limit": limits.memory_bytes,
            # Без своих лимитов Piston даёт компиляции 10 с по стенным часам и по процессорному
            # времени. Окружение без компиляции их не использует.
            "compile_timeout": self._compile_timeout_ms,
            "compile_cpu_time": self._compile_timeout_ms,
        }
        if spec.compile_memory_bytes is not None:
            body["compile_memory_limit"] = spec.compile_memory_bytes
        try:
            response = await self._http.post(
                "/api/v2/execute",
                json=body,
                timeout=(self._compile_timeout_ms + limits.timeout_ms) / 1000 + HTTP_MARGIN_SECONDS,
            )
        except httpx.HTTPError as error:
            raise SandboxServiceError(f"Piston недоступен: {error!r}") from error
        if response.status_code != 200:
            raise SandboxServiceError(
                f"Piston отказал ({response.status_code}): {_message(response)}"
            )
        return response.json()


def stage_result(stage: dict[str, Any]) -> RunResult:
    """Разбирает стадию compile или run из ответа Piston."""
    code = stage.get("code")
    signal = stage.get("signal")
    # Пакеты Piston запускают код через bash-скрипт run. Если процесс убит сигналом,
    # например OOM killer при превышении лимита памяти, bash завершается кодом 128 + N,
    # и Piston видит обычный выход без сигнала.
    if signal is None and code is not None and code > 128:
        try:
            signal = signals.Signals(code - 128).name
        except ValueError:
            pass
    wall = stage.get("wall_time")
    return RunResult(
        stdout=stage.get("stdout") or "",
        stderr=stage.get("stderr") or "",
        exit_code=code,
        signal=signal,
        # Статус TO Piston ставит и при лимите по стенным часам, и при лимите CPU.
        timed_out=stage.get("status") == "TO",
        # OL — лимит stdout, EL — лимит stderr.
        output_limited=stage.get("status") in ("OL", "EL"),
        wall_ms=round(wall) if wall is not None else None,
        memory_bytes=stage.get("memory"),
    )


def _compile_error(stage: dict[str, Any]) -> str:
    if stage.get("status") == "TO":
        return "превышено время компиляции"
    text = ((stage.get("stderr") or "") + (stage.get("stdout") or "")).strip()
    return text or f"компилятор завершился с кодом {stage.get('code')}"


def _message(response: httpx.Response) -> str:
    try:
        return str(response.json().get("message", response.text))
    except ValueError:
        return response.text
