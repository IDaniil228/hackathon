"""Статический анализ кода кандидата в песочнице (протокол CodeAnalyzer).

Инструменты — ruff, radon и разбор ast для Python, lizard, clang-tidy и cppcheck для C++ —
исполняются в служебном окружении analyzers песочницы драйвером
taskgen/sandbox/drivers/taskgen_analyze.py. Сервис только отправляет код и принимает отчёты:
на его машине нет инструмента, который разбирает код кандидата. Почему — раздел про
анализаторы в docs/taskgen/security.md.

Ключи метрик описаны в рубриках каталога rubrics: ключ "инструмент.метрика" в рубрике
совпадает с AnalysisReport.tool и ключом в metrics. Анализ не запустился или не дал отчёта —
один отчёт ANALYSIS_TOOL с пометкой в findings: балл качества тогда считается без метрик.
"""

from __future__ import annotations

import json

import httpx
from pydantic import ValidationError

from taskgen.contracts import AnalysisReport, RunLimits, RunRequest, SandboxRunner
from taskgen.evaluation.languages import source_language
from taskgen.sandbox.drivers import analysis_files
from taskgen.sandbox.errors import SandboxError
from taskgen.sandbox.registry import RuntimeRegistry

ANALYZERS_RUNTIME = "analyzers"
ANALYSIS_TOOL = "analysis"
# Каноническое имя языка → окружение, чьё имя файла решения получает анализируемый код:
# по расширению файла инструменты узнают язык.
LANGUAGE_RUNTIMES = {"python": "python", "cpp": "cpp"}
# Запас к бюджету инструментов: драйверу нужно время запуститься и напечатать отчёт.
MARGIN_MS = 5000
STDERR_TAIL = 300


def _note(text: str) -> list[AnalysisReport]:
    return [AnalysisReport(tool=ANALYSIS_TOOL, findings=[{"note": text}])]


class SandboxAnalyzer:
    """Запускает драйвер анализа в песочнице одним запуском на ответ."""

    def __init__(
        self,
        sandbox: SandboxRunner,
        *,
        timeout_seconds: float,
        memory_bytes: int,
        registry: RuntimeRegistry | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.timeout_seconds = timeout_seconds
        self.memory_bytes = memory_bytes
        self.registry = registry or RuntimeRegistry.load()

    async def analyze(self, code: str, language: str) -> list[AnalysisReport]:
        """Отчёты инструментов языка; язык без анализаторов — пустой список."""
        name = source_language(language)
        if name not in LANGUAGE_RUNTIMES:
            return []
        source = self.registry.get(LANGUAGE_RUNTIMES[name]).source_file
        request = RunRequest(
            runtime=ANALYZERS_RUNTIME,
            files=analysis_files(source, code),
            args=[name, source, f"{self.timeout_seconds:g}"],
            limits=RunLimits(
                timeout_ms=round(self.timeout_seconds * 1000) + MARGIN_MS,
                memory_bytes=self.memory_bytes,
            ),
        )
        try:
            result = await self.sandbox.run(request)
        except (SandboxError, httpx.HTTPError) as error:
            # Анализ — часть балла качества, а не приём ответа: без песочницы ответ сохраняется.
            return _note(f"анализ в песочнице не запущен: {error}")
        if result.timed_out:
            return _note("анализ не уложился во время песочницы")
        if result.exit_code != 0:
            tail = result.stderr.strip()[-STDERR_TAIL:]
            return _note(f"драйвер анализа завершился с кодом {result.exit_code}: {tail}")
        try:
            raw = json.loads(result.stdout)
            return [AnalysisReport.model_validate(item) for item in raw]
        except (ValueError, TypeError, ValidationError):
            return _note("драйвер анализа вернул не список отчётов")
