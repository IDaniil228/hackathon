"""Подделки песочницы для тестов генерации.

LocalSandbox исполняет код в локальном процессе Python: тесты проверяют настоящую обвязку
генератора и настоящий эталон, а не заранее заготовленные ответы. Изоляции здесь нет, поэтому
подделка годится только для шаблонов, которые написаны в самих тестах.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from taskgen.contracts import CaseOutcome, RunRequest, RunResult, TestReport, TestRunRequest

_FACTS_HEADER = "Факты, которые обязаны быть в ситуации:\n"
_CHECKER_FACT = re.compile(r"^- (?P<id>[^:\n]+): (?P<text>.+)$", re.MULTILINE)
_NUMBERED_SPAN = re.compile(r"^\[(\d+)\] (.+)$", re.MULTILINE)


def essay_reply(messages, schema, *, keep_facts: bool = True) -> str | None:
    """Ответ модели на запрос ситуации эссе или её проверки; None — запрос не про эссе.

    Писатель складывает ситуацию из фактов запроса (или пишет её без фактов), проверяльщик
    честно ищет каждый факт в ситуации и цитирует его или называет номера его участков.
    """
    properties = (schema or {}).get("properties", {})
    user = messages[1].content
    if "situation" in properties:
        listing = user.split(_FACTS_HEADER, 1)[1].split("\n\n", 1)[0]
        facts = [line.removeprefix("- ") for line in listing.splitlines()]
        situation = " ".join(facts) if keep_facts else "В команде что-то происходит."
        return json.dumps({"title": "Ситуация", "situation": situation}, ensure_ascii=False)
    if "facts" in properties:
        listing, situation = user.split("<<<СИТУАЦИЯ-", 1)
        numbered = dict((int(found[1]), found[2]) for found in _NUMBERED_SPAN.finditer(situation))
        by_spans = "spans" in properties["facts"]["items"]["properties"]
        items = []
        for match in _CHECKER_FACT.finditer(listing):
            text = match["text"]
            if by_spans:
                spans = [n for n, span in numbered.items() if span in text or text in span]
                items.append({"fact_id": match["id"], "present": bool(spans), "spans": spans})
            else:
                present = text in situation
                quotes = [text] if present else []
                items.append({"fact_id": match["id"], "present": present, "quotes": quotes})
        return json.dumps({"facts": items}, ensure_ascii=False)
    return None


_SOLVER_OPTION = re.compile(r"^- (?P<id>[a-z]): (?P<text>.+)$", re.MULTILINE)


def quiz_solver_reply(
    messages, schema, *, pick: str, also_correct: tuple[str, ...] = ()
) -> str | None:
    """Ответ решателя задачки; None — запрос не к решателю.

    Решатель выбирает вариант с текстом pick и называет верными его и варианты also_correct.
    Ключа он не видит: идентификаторы берёт из списка вариантов в запросе.
    """
    if "choice" not in (schema or {}).get("properties", {}):
        return None
    ids = {m["text"]: m["id"] for m in _SOLVER_OPTION.finditer(messages[-1].content)}
    correct = [ids[pick], *(ids[text] for text in also_correct)]
    return json.dumps({"choice": ids[pick], "correct": correct}, ensure_ascii=False)


def code_solver_reply(messages, schema, *, solution: str) -> str | None:
    """Ответ решателя задания с кодом; None — запрос не к решателю."""
    if "solution" not in (schema or {}).get("properties", {}):
        return None
    return json.dumps({"solution": solution}, ensure_ascii=False)


class BagOfWordsEmbedder:
    """Эмбеддер "мешок слов": одинаковые тексты дают сходство 1, тексты без общих слов — 0."""

    model_name = "bag-of-words"

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        vectors = []
        for text in texts:
            vector = [0.0] * 256
            for word in re.findall(r"\w+", text.casefold()):
                vector[sum(map(ord, word)) % 256] += 1.0
            vectors.append(vector)
        return vectors


class GatedLLM:
    """LLMClient, который пропускает первые free вызовов, а остальные держит до open().

    Так тест видит сессию в момент, когда первое задание готово, а остальные ещё пишутся.
    """

    def __init__(self, inner: Any, *, free: int) -> None:
        self.inner = inner
        self.free = free
        self._gate: asyncio.Event | None = None

    def _event(self) -> asyncio.Event:
        # Событие создаётся в цикле приложения, а не в потоке теста.
        if self._gate is None:
            self._gate = asyncio.Event()
        return self._gate

    async def complete(self, messages, **kwargs) -> str:
        if self.free > 0:
            self.free -= 1
        else:
            await self._event().wait()
        return await self.inner.complete(messages, **kwargs)

    def open(self) -> None:
        self._event().set()

    async def aclose(self) -> None:
        """Приложение закрывает клиента LLM при остановке."""


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
            except asyncio.CancelledError:
                # Остановка приложения отменяет фоновую генерацию; процесс не должен пережить
                # цикл событий, иначе его транспорт закрывается уже в закрытом цикле.
                process.kill()
                await asyncio.shield(process.wait())
                raise
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
