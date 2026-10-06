"""Проверки условия, которое написала LLM.

Модель видит только публичные примеры, поэтому скрытый кейс в условии — совпадение или
утечка через раздел «Генерация». В обоих случаях условие подсказывает скрытый тест, и его
надо переписать.
"""

from __future__ import annotations

import json
import re
from typing import Any

from taskgen.contracts import TestCase

# Короткие значения вроде 70 или [1,2] встречаются в любом тексте; утечкой считается только
# совпадение не короче этого числа символов после нормализации.
MIN_FINGERPRINT = 5

_SPACE = re.compile(r"\s+")


def public_count(total: int, limit: int) -> int:
    """Сколько первых кейсов показать кандидату: не больше limit, но хотя бы один скрытый.

    Единственный кейс показывается: задание без примера понять труднее, чем потерять
    единственный скрытый тест.
    """
    return min(limit, max(total - 1, 1))


def mentions_entrypoint(text: str, entrypoint: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(entrypoint)}(?![A-Za-z0-9_])", text) is not None


def _normalize(text: str) -> str:
    # Модель может переформатировать значение: пробелы, одинарные кавычки, True вместо true.
    return _SPACE.sub("", text).replace("'", '"').lower()


def _render(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _fingerprints(case: TestCase) -> set[str]:
    values: list[Any] = [case.input, case.expected]
    if isinstance(case.input, list):
        values += case.input
    elif isinstance(case.input, dict):
        values += [json.dumps(case.input, ensure_ascii=False), *case.input.values()]
    return {_normalize(_render(value)) for value in values}


def hidden_leaks(text: str, public: list[TestCase], hidden: list[TestCase]) -> list[str]:
    """Значения скрытых кейсов, которые встречаются в тексте.

    Значение, входящее в публичный пример, не считается утечкой: модель видела его и могла
    процитировать.
    """
    normalized = _normalize(text)
    shown = [fp for case in public for fp in _fingerprints(case)]
    leaks = set()
    for case in hidden:
        for fp in _fingerprints(case):
            if len(fp) < MIN_FINGERPRINT or any(fp in item for item in shown):
                continue
            if fp in normalized:
                leaks.add(fp)
    return sorted(leaks)
