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

Кейс — {"input": [аргументы entrypoint], "expected": значение}. input — массив позиционных
аргументов, для Python допустим и объект именованных; так его понимает песочница в run_tests.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from taskgen.contracts import RunLimits, RunRequest, RunResult, TestCase

HARNESS_FILE = "main.py"

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
    _text = json.dumps(list(_cases), ensure_ascii=False)
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


def parse_generator_output(result: RunResult) -> list[TestCase]:
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
    for number, item in enumerate(raw, start=1):
        if not isinstance(item, dict) or "input" not in item or "expected" not in item:
            raise GeneratorError(f"кейс {number}: нужен объект с полями input и expected")
        if not isinstance(item["input"], list | dict):
            raise GeneratorError(
                f"кейс {number}: input — массив аргументов функции или объект "
                "именованных аргументов"
            )
        try:
            cases.append(TestCase.model_validate(item))
        except ValidationError as error:
            raise GeneratorError(f"кейс {number}: {error}") from error
    return cases
