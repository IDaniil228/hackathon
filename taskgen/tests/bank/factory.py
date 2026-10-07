"""Сборка md-шаблонов для тестов: валидный шаблон и точечные поломки поверх него."""

from __future__ import annotations

from typing import Any

import yaml

from taskgen.bank.parser import TemplateIssue
from taskgen.bank.validation import TemplateValidator
from taskgen.config import ImportCheckSettings, ScoringSettings
from taskgen.contracts import RunLimits, TemplateSpec
from tests.generation.fakes import LocalSandbox

DEFAULT_FRONTMATTER: dict[str, Any] = {
    "name": "algo-two-sum-sorted",
    "description": "Поиск пары элементов с заданной суммой в отсортированном массиве",
    "area": "algo",
    "grade": "junior",
    "skills": ["python", "two-pointers"],
    "kind": "code",
    "runtime": "python",
    "io": "function",
    "difficulty": 3,
    "expected_minutes": 15,
    "params": {
        "n": {"type": "int", "min": 1000, "max": 5000},
        "unique": {"type": "choice", "choices": [True, False]},
    },
    "case_groups": {"main": {"weight": 1, "required": True}},
}

# Строки «## » и «### » внутри блока кода не начинают ни раздел, ни решение.
REFERENCE = (
    "## Два указателя с концов массива\n"
    "def two_sum(nums, target):\n"
    "    ### i и j сходятся к середине\n"
    "    i, j = 0, len(nums) - 1\n"
    "    while i < j:\n"
    "        s = nums[i] + nums[j]\n"
    "        if s == target:\n"
    "            return [i, j]\n"
    "        if s < target:\n"
    "            i += 1\n"
    "        else:\n"
    "            j -= 1\n"
    "    return []\n"
)
SHORT_RIGHT = REFERENCE.replace("len(nums) - 1", "len(nums) - 2")


def solution(solution_id: str, mark: str, code: str, description: str = "") -> str:
    text = f"\n\n{description}" if description else ""
    return f"### {solution_id} {mark}{text}\n\n```python\n{code}```"


SOLUTIONS = (
    solution("reference", "+", REFERENCE)
    + "\n\n"
    + solution("short-right", "-", SHORT_RIGHT, "Правый указатель стартует с предпоследнего")
)

CODE_SECTIONS: dict[str, str] = {
    "Генерация": "Придумай легенду про склад. Массив длины {n}, уникальность: {unique}.",
    "Решения": SOLUTIONS,
    "Сигнатура": "```\ntwo_sum\n```",
    "Тесты": (
        "```python\n"
        "def generate(params, rng):\n"
        "    cases = [\n"
        "        {'input': [[1, 2, 3], 5], 'expected': [1, 2]},\n"
        "        {'input': [[1, 2, 3, 9], 12], 'expected': [2, 3]},\n"
        "        {'input': [[2, 7], 9], 'expected': [0, 1]},\n"
        "        {'input': [[1, 2], 10], 'expected': []},\n"
        "        {'input': [[], 0], 'expected': []},\n"
        "        {'input': [[-3, 0, 4, 8], 5], 'expected': [0, 3]},\n"
        "    ]\n"
        "    return [dict(case, group='main') for case in cases]\n"
        "```"
    ),
    "Рубрика": (
        "- linear: решение работает за линейное время\n"
        "  + два указателя с концов массива\n"
        "  - вложенный цикл по всем парам\n"
        "- edge: учтён случай, когда пары нет"
    ),
}

# Задача stdio: программа читает числа из stdin и печатает сумму каждой строки.
STDIO_REFERENCE = (
    "import sys\nfor line in sys.stdin:\n    print(sum(int(token) for token in line.split()))\n"
)
# Пустая строка входа даёт 0, а неверное решение её пропускает.
STDIO_SKIPS_EMPTY = (
    "import sys\n"
    "for line in sys.stdin:\n"
    "    if line.strip():\n"
    "        print(sum(int(token) for token in line.split()))\n"
)

STDIO_SECTIONS: dict[str, str] = {
    "Генерация": "Придумай легенду про кассу: в каждой строке входа чеки одного покупателя.",
    "Решения": (
        solution("reference", "+", STDIO_REFERENCE)
        + "\n\n"
        + solution("skips-empty", "-", STDIO_SKIPS_EMPTY, "Пропускает покупателя без чеков")
    ),
    "Тесты": (
        "```python\n"
        "def generate(params, rng):\n"
        "    rows = [[1, 2], [], [5], [-3, 3, 10], [], [7, 7, 7]]\n"
        "    cases = []\n"
        "    for size in range(1, len(rows) + 1):\n"
        "        lines = rows[:size]\n"
        "        stdin = ''.join(' '.join(map(str, row)) + '\\n' for row in lines)\n"
        "        expected = ''.join(f'{sum(row)}\\n' for row in lines)\n"
        "        cases.append({'stdin': stdin, 'expected': expected, 'group': 'main'})\n"
        "    return cases\n"
        "```"
    ),
    "Рубрика": "- stream: вход читается построчно, без загрузки целиком",
}


def make_stdio(**frontmatter: Any) -> str:
    """Валидный шаблон задачи stdio на Python."""
    frontmatter.setdefault("io", "stdio")
    frontmatter.setdefault("params", {})
    sections = dict(STDIO_SECTIONS)
    sections |= frontmatter.pop("sections", {})
    sections.setdefault("Сигнатура", None)
    return make_template(sections=sections, **frontmatter)


ESSAY_SECTIONS: dict[str, str] = {
    "Генерация": "Опиши команду, которая выбирает, чем распараллелить сервис.",
    "Вопросы": "1. Чем процесс отличается от потока и что выбрать этой команде?",
    "Факты": "- cpu-bound: сервис почти всё время считает на процессоре",
    "Нельзя раскрывать": "- общая память\n- мьютекс",
    "Рубрика": "- memory: сказано про общую память потоков",
}

QUIZ_VARIANTS = """### positive

Факты:
- weights: время проезда по каждой дороге — положительное число минут
- size: в городе {n} перекрёстков

Ответ:
+ Алгоритм Дейкстры
- Обход в ширину
- Алгоритм Беллмана — Форда
- Алгоритм Флойда — Уоршелла

### unit

Факты:
- weights: проезд по любой дороге занимает ровно одну минуту
- size: в городе {n} перекрёстков

Ответ:
+ Обход в ширину
- Алгоритм Дейкстры
- Алгоритм Беллмана — Форда
- Алгоритм Флойда — Уоршелла"""

QUIZ_SECTIONS: dict[str, str] = {
    "Генерация": "Опиши диспетчерскую такси, которая ищет быстрый путь до клиента.",
    "Вопрос": "Какой алгоритм быстрее всех даст верные кратчайшие пути от диспетчерской?",
    "Варианты": QUIZ_VARIANTS,
    "Нельзя раскрывать": "- дейкстр\n- в ширину",
}


def make_template(
    *,
    sections: dict[str, str | None] | None = None,
    drop: tuple[str, ...] = (),
    **frontmatter: Any,
) -> str:
    """Валидный шаблон с заменёнными полями. Значение None убирает поле или раздел."""
    meta = dict(DEFAULT_FRONTMATTER)
    meta.update(frontmatter)
    for key in drop:
        meta.pop(key, None)
    meta = {k: v for k, v in meta.items() if v is not None}
    base = {"essay": ESSAY_SECTIONS, "quiz": QUIZ_SECTIONS}.get(meta.get("kind"), CODE_SECTIONS)
    merged: dict[str, str | None] = dict(base)
    merged.update(sections or {})
    body = "\n\n".join(
        f"## {title}\n\n{text}" for title, text in merged.items() if text is not None
    )
    head = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
    return f"---\n{head}---\n\n{body}\n"


def local_check(sandbox: LocalSandbox | None = None, seeds: int = 1) -> TemplateValidator:
    """Проверка импорта на LocalSandbox: настоящие генератор и решения без Piston."""
    return TemplateValidator(
        LocalSandbox() if sandbox is None else sandbox,
        check=ImportCheckSettings(seeds=seeds),
        limits=RunLimits(timeout_ms=5000, memory_bytes=256 * 1024 * 1024),
        scoring=ScoringSettings(),
    )


class Unchecked:
    """Импорт без проверки в песочнице. Нужен только тестам, которые кладут в банк заведомо
    сломанный шаблон, чтобы проверить страховку при генерации экземпляра."""

    async def check(self, templates: list[tuple[str, TemplateSpec]]) -> list[TemplateIssue]:
        return []


def make_essay(**frontmatter: Any) -> str:
    frontmatter.setdefault("kind", "essay")
    frontmatter.setdefault("runtime", None)
    frontmatter.setdefault("io", None)
    frontmatter.setdefault("case_groups", None)
    frontmatter.setdefault("params", {})
    return make_template(**frontmatter)


def make_quiz(**frontmatter: Any) -> str:
    frontmatter.setdefault("kind", "quiz")
    frontmatter.setdefault("runtime", None)
    frontmatter.setdefault("io", None)
    frontmatter.setdefault("case_groups", None)
    frontmatter.setdefault("params", {"n": {"type": "int", "min": 50, "max": 90}})
    return make_template(**frontmatter)
