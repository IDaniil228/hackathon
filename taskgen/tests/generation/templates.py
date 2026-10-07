"""Шаблоны для тестов генерации: генератор зависит от параметров и seed."""

from __future__ import annotations

from typing import Any

from taskgen.bank.parser import parse_template
from taskgen.contracts import TemplateSpec
from tests.bank.factory import make_essay, make_quiz, make_template, solution

ENTRYPOINT = "top_sum"

REFERENCE = "def top_sum(nums, k):\n    return sum(sorted(nums, reverse=True)[:k])\n"
SMALLEST = "def top_sum(nums, k):\n    return sum(sorted(nums)[:k])\n"


def solutions_section(reference: str = REFERENCE) -> str:
    """Раздел «Решения»: reference — первое верное, к нему одно заведомо неверное."""
    return (
        solution("reference", "+", reference)
        + "\n\n"
        + solution("smallest", "-", SMALLEST, "Сумма k наименьших чисел вместо наибольших")
    )


GENERATOR = (
    "```python\n"
    "def generate(params, rng):\n"
    "    cases = []\n"
    "    for _ in range(params['count']):\n"
    "        nums = [rng.randint(-10**6, 10**6) for _ in range(params['n'])]\n"
    "        k = rng.randint(1, params['n'])\n"
    "        cases.append({'input': [nums, k], 'group': 'main',\n"
    "                      'expected': sum(sorted(nums, reverse=True)[:k])})\n"
    "    return cases\n"
    "```"
)

PARAMS: dict[str, Any] = {
    "n": {"type": "int", "min": 5, "max": 9},
    "count": {"type": "int", "min": 6, "max": 8},
    "topic": {"type": "choice", "choices": ["склад", "банк", "космопорт"]},
}

GENERATION = "Придумай легенду про {topic}. Массив из {n} чисел, выбрать {{k}} наибольших."


def code_template_md(**frontmatter: Any) -> str:
    frontmatter.setdefault("params", PARAMS)
    sections = {
        "Генерация": GENERATION,
        "Решения": solutions_section(),
        "Сигнатура": f"```\n{ENTRYPOINT}\n```",
        "Тесты": GENERATOR,
    }
    sections |= frontmatter.pop("sections", {})
    return make_template(sections=sections, **frontmatter)


def essay_template_md(**frontmatter: Any) -> str:
    frontmatter.setdefault("params", {"topic": {"type": "choice", "choices": ["GIL", "asyncio"]}})
    return make_essay(
        sections={"Генерация": "Спроси кандидата про {topic} в Python."}, **frontmatter
    )


def code_spec(**frontmatter: Any) -> TemplateSpec:
    return parse_template(code_template_md(**frontmatter), source="тест")


def essay_spec(**frontmatter: Any) -> TemplateSpec:
    return parse_template(essay_template_md(**frontmatter), source="тест")


def quiz_template_md(**frontmatter: Any) -> str:
    return make_quiz(**frontmatter)


def quiz_spec(**frontmatter: Any) -> TemplateSpec:
    return parse_template(quiz_template_md(**frontmatter), source="тест")
