"""Сравнение ответа решения с ожидаемым значением.

Сравнение идёт вне песочницы: ожидаемые значения не попадают к коду кандидата, поэтому
код не может их прочитать или подменить результат сравнения.
"""

from __future__ import annotations

import math
from typing import Any

# Относительный допуск покрывает накопленную ошибку округления, абсолютный — ответы около нуля.
REL_TOL = 1e-6
ABS_TOL = 1e-9


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
