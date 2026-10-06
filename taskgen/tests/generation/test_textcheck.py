"""Проверки текста от LLM-писателя: дефект отклоняется, честный текст — нет.

Дефектные фрагменты взяты из настоящих выводов моделей на шаблонах банка. Честный корпус —
шаблоны банка и ситуации эссе из бенчмарка проверки: в них есть блоки кода, формулы, списки
и примеры вызовов, и ни одна проверка не должна на них сработать.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from taskgen.generation.textcheck import text_problems

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = sorted((ROOT / "seed" / "templates").rglob("*.md"))
BENCH = json.loads((ROOT / "tests" / "generation" / "bench" / "cases.json").read_text("utf-8"))


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda path: path.stem)
def test_template_text_has_no_problems(path):
    assert text_problems(path.read_text(encoding="utf-8")) == []


def test_bench_situations_have_no_problems():
    flagged = {
        case["id"]: problems
        for case in BENCH
        if (problems := text_problems(f"{case['title']}\n\n{case['situation']}"))
    }
    assert flagged == {}


def test_unclosed_code_block_is_a_cut_off():
    # Saiga закрыла строку JSON посреди примера в блоке кода.
    [problem] = text_problems("Пример использования:\n```python\ninput = [[")
    assert "блока кода" in problem
