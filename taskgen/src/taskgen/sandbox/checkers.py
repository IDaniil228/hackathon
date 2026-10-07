"""Встроенные режимы проверки вывода решения: сравнение с ожидаемым выводом в сервисе.

Сравнение идёт вне песочницы: ожидаемые значения не попадают к коду кандидата, поэтому
код не может их прочитать или подменить результат сравнения. Режим checker — своя
программа шаблона, она исполняется в песочнице (taskgen.sandbox.cases), а не здесь.

Режимы (CheckMode в contracts.py):

- tokens — вывод и ожидаемое режутся по пробельным символам, токены равны попарно;
- exact — строки равны побайтно, включая пробелы и переводы строк;
- float — как tokens, но токены-числа сравниваются с допуском REL_TOL и ABS_TOL;
- json — оба текста разбираются как JSON и сравниваются функцией values_match.
"""

from __future__ import annotations

import json
import math
from typing import Any

from taskgen.contracts import CheckMode

# Относительный допуск покрывает накопленную ошибку округления, абсолютный — ответы около нуля.
REL_TOL = 1e-6
ABS_TOL = 1e-9


def output_matches(mode: CheckMode, output: str, expected: str) -> bool:
    """Совпадает ли вывод решения с ожидаемым выводом в одном из встроенных режимов."""
    if mode == "tokens":
        return output.split() == expected.split()
    if mode == "exact":
        return output == expected
    if mode == "float":
        actual, wanted = output.split(), expected.split()
        return len(actual) == len(wanted) and all(map(_tokens_match, actual, wanted))
    if mode == "json":
        try:
            return values_match(json.loads(output), json.loads(expected))
        except ValueError:
            return False
    raise ValueError(f"режим {mode} — не встроенное сравнение: checker исполняется в песочнице")


def _tokens_match(actual: str, expected: str) -> bool:
    try:
        left, right = float(actual), float(expected)
    except ValueError:
        return actual == expected
    # inf и nan не числа задачи, а слова: допуск к ним не применяется.
    if not (math.isfinite(left) and math.isfinite(right)):
        return actual == expected
    return math.isclose(left, right, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def values_match(actual: Any, expected: Any) -> bool:
    """Сравнивает JSON-совместимые значения; числа с плавающей точкой — с допуском."""
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(actual) is type(expected) and actual == expected
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        if isinstance(expected, int) and isinstance(actual, int):
            return actual == expected
        return math.isclose(actual, expected, rel_tol=REL_TOL, abs_tol=ABS_TOL)
    if isinstance(expected, list) and isinstance(actual, list):
        return len(actual) == len(expected) and all(map(values_match, actual, expected))
    if isinstance(expected, dict) and isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(
            values_match(actual[key], expected[key]) for key in expected
        )
    return type(actual) is type(expected) and actual == expected
