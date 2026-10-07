"""Запуск генератора тестов шаблона в песочнице.

Раздел «Тесты» — код из загруженного шаблона, поэтому сервис его не исполняет: код уходит
в песочницу вместе с обвязкой. Контракт запуска:

- runtime python, один файл main.py — обвязка; исполняемый файл не зависит от того, как
  песочница раскладывает несколько файлов;
- stdin — JSON {"source": код раздела, "params": параметры экземпляра, "seed": seed задания};
- обвязка исполняет source в отдельном пространстве имён, вызывает
  generate(params, random.Random(seed)) и печатает в stdout JSON-список кейсов;
- print внутри generate уходит в stderr и не портит вывод; ошибка генератора даёт
  ненулевой код выхода и traceback в stderr.

Кейс задачи io=function — {"input": [аргументы entrypoint], "expected": значение}. input —
массив позиционных аргументов, для Python допустим и объект именованных; так его понимает
драйвер в песочнице. Кейс задачи io=stdio — {"stdin": текст, "expected": текст вывода} и
необязательно "files": {имя: текст} — файлы рядом с программой. Если шаблон объявил группы
кейсов, у каждого кейса есть ещё "group" — имя одной из них.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from taskgen.contracts import CaseGroup, IOKind, RunLimits, RunRequest, RunResult, TestCase

HARNESS_FILE = "main.py"
# Имя файла кейса — одно имя в каталоге запуска: без каталогов и без скрытых файлов.
FILE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")

HARNESS = """\
import contextlib
import json
import random
import sys

_payload = json.loads(sys.stdin.read())
_namespace = {"__name__": "taskgen_generator"}
exec(compile(_payload["source"], "generator.py", "exec"), _namespace)
if not callable(_namespace.get("generate")):
    sys.exit("в коде генератора нет функции generate(params, rng)")
with contextlib.redirect_stdout(sys.stderr):
    _cases = _namespace["generate"](_payload["params"], random.Random(_payload["seed"]))
try:
    _text = json.dumps(list(_cases), ensure_ascii=False, allow_nan=False)
except (TypeError, ValueError) as _error:
    sys.exit(f"кейсы генератора не переводятся в JSON: {_error}")
sys.stdout.write(_text)
"""

# Хвост stderr в сообщении: traceback генератора длинный, а причина — в последних строках.
_STDERR_TAIL = 1000


class GeneratorError(Exception):
    """Генератор тестов шаблона упал или вернул кейсы не того вида."""


def generator_request(
    source: str, params: dict[str, Any], seed: int, limits: RunLimits
) -> RunRequest:
    payload = {"source": source, "params": params, "seed": seed}
    return RunRequest(
        runtime="python",
        files={HARNESS_FILE: HARNESS},
        stdin=json.dumps(payload, ensure_ascii=False),
        limits=limits,
    )


def parse_generator_output(
    result: RunResult, groups: Mapping[str, CaseGroup] | None = None, *, io: IOKind
) -> list[TestCase]:
    """Кейсы из вывода генератора; groups — TemplateSpec.case_groups, io — вид задания."""
    cases = _parse_cases(result, io)
    _check_groups(cases, groups or {})
    return cases


def _check_groups(cases: list[TestCase], groups: Mapping[str, CaseGroup]) -> None:
    for number, case in enumerate(cases, start=1):
        if case.group is None:
            if groups:
                raise GeneratorError(f"кейс {number} без группы, а шаблон объявил группы кейсов")
        elif case.group not in groups:
            declared = ", ".join(groups) or "нет ни одной"
            raise GeneratorError(
                f"кейс {number}: группа {case.group} не объявлена в case_groups; "
                f"объявлены: {declared}"
            )
    present = {case.group for case in cases}
    empty = [name for name, group in groups.items() if group.required and name not in present]
    if empty:
        raise GeneratorError(f"обязательные группы без кейсов: {', '.join(empty)}")


def _parse_cases(result: RunResult, io: IOKind) -> list[TestCase]:
    if result.timed_out:
        raise GeneratorError("генератор тестов не уложился во время прогона")
    if result.exit_code != 0:
        stderr = result.stderr.strip()[-_STDERR_TAIL:] or f"сигнал {result.signal}"
        raise GeneratorError(f"генератор тестов завершился с ошибкой: {stderr}")
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise GeneratorError(f"вывод генератора тестов — не JSON: {error.msg}") from error
    if not isinstance(raw, list):
        raise GeneratorError("генератор тестов должен вернуть список кейсов")
    if not raw:
        raise GeneratorError("генератор тестов вернул пустой список кейсов")
    cases = []
    check = _check_function_case if io is IOKind.FUNCTION else _check_stdio_case
    fields = "input и expected" if io is IOKind.FUNCTION else "stdin и expected"
    for number, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise GeneratorError(f"кейс {number}: нужен объект с полями {fields}")
        check(number, item)
        if not isinstance(item.get("group", ""), str):
            raise GeneratorError(f"кейс {number}: group — имя группы строкой")
        try:
            cases.append(TestCase.model_validate(item))
        except ValidationError as error:
            raise GeneratorError(f"кейс {number}: {error}") from error
    return cases


def _check_function_case(number: int, item: dict[str, Any]) -> None:
    if "input" not in item or "expected" not in item:
        raise GeneratorError(f"кейс {number}: нужен объект с полями input и expected")
    if not isinstance(item["input"], list | dict):
        raise GeneratorError(
            f"кейс {number}: input — массив аргументов функции или объект именованных аргументов"
        )
    extra = sorted({"stdin", "files"} & set(item))
    if extra:
        raise GeneratorError(f"кейс {number}: поле {extra[0]} — только у задания io=stdio")


def _check_stdio_case(number: int, item: dict[str, Any]) -> None:
    if not isinstance(item.get("stdin"), str):
        raise GeneratorError(f"кейс {number}: stdin — вход программы строкой")
    if not isinstance(item.get("expected"), str):
        raise GeneratorError(f"кейс {number}: expected — ожидаемый вывод программы строкой")
    if "input" in item:
        raise GeneratorError(f"кейс {number}: поле input — только у задания io=function")
    files = item.get("files", {})
    if not isinstance(files, dict) or not all(isinstance(v, str) for v in files.values()):
        raise GeneratorError(f"кейс {number}: files — объект имя файла → текст")
    for name in files:
        if not FILE_NAME.match(name):
            raise GeneratorError(
                f"кейс {number}: имя файла {name!r} — одно имя без каталогов, не скрытое"
            )
