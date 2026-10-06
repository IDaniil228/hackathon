"""Сборка md-шаблонов для тестов: валидный шаблон и точечные поломки поверх него."""

from __future__ import annotations

from typing import Any

import yaml

DEFAULT_FRONTMATTER: dict[str, Any] = {
    "name": "algo-two-sum-sorted",
    "description": "Поиск пары элементов с заданной суммой в отсортированном массиве",
    "area": "algo",
    "grade": "junior",
    "skills": ["python", "two-pointers"],
    "kind": "code",
    "runtime": "python",
    "difficulty": 3,
    "expected_minutes": 15,
    "params": {
        "n": {"type": "int", "min": 1000, "max": 5000},
        "unique": {"type": "choice", "choices": [True, False]},
    },
}

CODE_SECTIONS: dict[str, str] = {
    "Генерация": "Придумай легенду про склад. Массив длины {n}, уникальность: {unique}.",
    "Эталон": (
        "```python\n"
        "## Два указателя с концов массива\n"
        "def two_sum(nums, target):\n"
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
        "```"
    ),
    "Сигнатура": "```\ntwo_sum\n```",
    "Тесты": (
        "```python\n"
        "def generate(params, rng):\n"
        "    return [{'input': [[1, 2, 3], 5], 'expected': [1, 2]}]\n"
        "```"
    ),
    "Рубрика": (
        "- linear: решение работает за линейное время\n"
        "  + два указателя с концов массива\n"
        "  - вложенный цикл по всем парам\n"
        "- edge: учтён случай, когда пары нет"
    ),
}

ESSAY_SECTIONS: dict[str, str] = {
    "Генерация": "Опиши команду, которая выбирает, чем распараллелить сервис.",
    "Вопросы": "1. Чем процесс отличается от потока и что выбрать этой команде?",
    "Факты": "- cpu-bound: сервис почти всё время считает на процессоре",
    "Нельзя раскрывать": "- общая память\n- мьютекс",
    "Рубрика": "- memory: сказано про общую память потоков",
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
    base = CODE_SECTIONS if meta.get("kind", "code") == "code" else ESSAY_SECTIONS
    merged: dict[str, str | None] = dict(base)
    merged.update(sections or {})
    body = "\n\n".join(
        f"## {title}\n\n{text}" for title, text in merged.items() if text is not None
    )
    head = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
    return f"---\n{head}---\n\n{body}\n"


def make_essay(**frontmatter: Any) -> str:
    frontmatter.setdefault("kind", "essay")
    frontmatter.setdefault("runtime", None)
    frontmatter.setdefault("params", {})
    return make_template(**frontmatter)
