"""Публичные примеры задания с кодом текстом: для писателя условия и для слепого решателя.

Оба видят примеры в одной записи, поэтому решатель решает ровно то, что увидел бы
кандидат по условию и примерам.
"""

from __future__ import annotations

import json

from taskgen.contracts import IOKind, TestCase


def render_examples(io: IOKind, cases: list[TestCase]) -> str:
    if io is IOKind.FUNCTION:
        lines = "\n".join(
            f"- input: {json.dumps(c.input, ensure_ascii=False)}; "
            f"expected: {json.dumps(c.expected, ensure_ascii=False)}"
            for c in cases
        )
        return f"Примеры (input — аргументы функции, expected — результат):\n{lines}"
    blocks = []
    for number, case in enumerate(cases, start=1):
        parts = [f"Пример {number}", "stdin:", _fenced(case.stdin or "")]
        for name, content in case.files.items():
            parts += [f"файл {name}:", _fenced(content)]
        parts += ["ожидаемый вывод:", _fenced(str(case.expected))]
        blocks.append("\n".join(parts))
    return "Примеры (вход программы и её ожидаемый вывод):\n\n" + "\n\n".join(blocks)


def _fenced(text: str) -> str:
    # Забор длиннее любой серии обратных кавычек внутри, чтобы текст не закрыл блок.
    fence = "`" * max(3, _longest_backticks(text) + 1)
    return f"{fence}\n{text.rstrip(chr(10))}\n{fence}"


def _longest_backticks(text: str) -> int:
    best = current = 0
    for char in text:
        current = current + 1 if char == "`" else 0
        best = max(best, current)
    return best
