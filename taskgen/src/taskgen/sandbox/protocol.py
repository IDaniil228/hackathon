"""Протокол между сервисом и обвязкой тестов внутри песочницы.

Вход обвязки — stdin из двух строк: nonce и JSON-массив входов кейсов. Вход кейса — массив
позиционных аргументов функции; в Python ещё допустим объект именованных аргументов.
Ожидаемые значения в песочницу не передаются: сравнение идёт в сервисе, поэтому код
кандидата не может ни прочитать ответы, ни подменить вердикт.

Выход — строки "<nonce> <JSON>" в исходный stdout процесса. Вывод решения обвязка уводит
в /dev/null, а строки без nonce сервис отбрасывает. Записи:

- {"load_error": текст} — решение на Python не загрузилось; в C++ это ошибка компиляции
- {"case": i, "ok": true, "value": ответ, "ms": время вызова в миллисекундах}
- {"case": i, "ok": false, "error": текст}
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

from taskgen.contracts import CaseOutcome, RunLimits, TestCase, TestReport
from taskgen.sandbox.checkers import values_match

# Пик памяти, начиная с которого убийство процесса приписывается лимиту памяти. Piston
# переводит байты в килобайты и обратно с округлением, поэтому пик не равен лимиту точно.
MEMORY_KILL_SHARE = 0.9


@dataclass
class HarnessOutput:
    load_error: str | None = None
    records: dict[int, dict[str, Any]] = field(default_factory=dict)
    duplicated: set[int] = field(default_factory=set)


def build_stdin(nonce: str, cases: list[TestCase]) -> str:
    return f"{nonce}\n{json.dumps([case.input for case in cases], ensure_ascii=False)}\n"


def parse_output(stdout: str, nonce: str) -> HarnessOutput:
    output = HarnessOutput()
    prefix = nonce + " "
    for line in stdout.splitlines():
        if not line.startswith(prefix):
            continue
        try:
            record = json.loads(line[len(prefix) :])
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        if "load_error" in record:
            output.load_error = str(record["load_error"])
            continue
        index = record.get("case")
        if not isinstance(index, int):
            continue
        if index in output.records:
            output.duplicated.add(index)
        output.records[index] = record
    return output


def build_report(
    cases: list[TestCase], output: HarnessOutput, run: dict[str, Any], limits: RunLimits
) -> TestReport:
    """Сводит записи обвязки и итог стадии run из ответа Piston в отчёт."""
    if output.load_error is not None:
        return failed_report(cases, output.load_error)
    missing_reason = _missing_reason(run, limits)
    outcomes = []
    for index, case in enumerate(cases):
        record = output.records.get(index)
        if index in output.duplicated:
            outcomes.append(CaseOutcome(passed=False, error="нарушен протокол обвязки"))
        elif record is None:
            outcomes.append(CaseOutcome(passed=False, error=missing_reason))
        elif not record.get("ok"):
            outcomes.append(CaseOutcome(passed=False, error=str(record.get("error"))))
        else:
            passed = values_match(record.get("value"), case.expected)
            outcomes.append(
                CaseOutcome(
                    passed=passed,
                    wall_ms=_milliseconds(record.get("ms")),
                    error=None if passed else "неверный ответ",
                )
            )
    times = [o.wall_ms for o in outcomes if o.wall_ms is not None]
    return TestReport(
        outcomes=outcomes,
        max_wall_ms=max(times) if times else None,
        max_memory_bytes=run.get("memory"),
    )


def failed_report(cases: list[TestCase], compile_error: str) -> TestReport:
    # Исходов столько же, сколько кейсов, чтобы pass_rate и разбор по кейсам не зависели
    # от того, на какой стадии остановился прогон.
    return TestReport(
        outcomes=[CaseOutcome(passed=False, error="ошибка компиляции") for _ in cases],
        compile_error=compile_error,
    )


def _missing_reason(run: dict[str, Any], limits: RunLimits) -> str:
    status = run.get("status")
    if status == "TO":
        return "превышен лимит времени"
    if status in ("OL", "EL"):
        return "превышен лимит вывода"
    memory = run.get("memory") or 0
    killed = run.get("signal") == "SIGKILL" or run.get("code") == 128 + 9
    if killed and memory >= MEMORY_KILL_SHARE * limits.memory_bytes:
        return "превышен лимит памяти"
    stderr = (run.get("stderr") or "").strip()
    tail = stderr[-500:]
    return "решение оборвало прогон" + (f": {tail}" if tail else "")


def _milliseconds(value: Any) -> int | None:
    if not isinstance(value, int | float) or not math.isfinite(value):
        return None
    return max(0, round(value))
