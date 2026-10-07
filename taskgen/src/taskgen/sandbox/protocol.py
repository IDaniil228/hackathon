"""Протокол между сервисом и драйвером задач-функций внутри песочницы.

Вход драйвера — stdin из двух строк: nonce и JSON-массив входов кейсов. Вход кейса — массив
позиционных аргументов функции; в Python ещё допустим объект именованных аргументов.
Ожидаемые значения в песочницу не передаются: сверяет ответы ядро в сервисе
(taskgen/sandbox/cases.py), поэтому код кандидата не может ни прочитать ответы, ни
подменить вердикт.

Выход — строки "<nonce> <JSON>" в исходный stdout процесса. Вывод решения драйвер уводит
в /dev/null, а строки без nonce сервис отбрасывает: nonce нужен потому, что функция
кандидата исполняется в одном процессе с драйвером и могла бы напечатать запись сама.
Записи:

- {"load_error": текст} — решение на Python не загрузилось; в C++ это ошибка компиляции
- {"case": i, "ok": true, "value": ответ, "ms": время вызова в миллисекундах}
- {"case": i, "ok": false, "error": текст}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from taskgen.contracts import TestCase


@dataclass
class DriverOutput:
    load_error: str | None = None
    records: dict[int, dict[str, Any]] = field(default_factory=dict)
    duplicated: set[int] = field(default_factory=set)


def build_stdin(nonce: str, cases: list[TestCase]) -> str:
    return f"{nonce}\n{json.dumps([case.input for case in cases], ensure_ascii=False)}\n"


def parse_output(stdout: str, nonce: str) -> DriverOutput:
    output = DriverOutput()
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
