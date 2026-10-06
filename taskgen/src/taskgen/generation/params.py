"""Параметры экземпляра: значения выбирает код по seed, а не LLM.

Сложность экземпляра задаёт шаблон через диапазоны параметров. Если бы значения выбирала
модель, она могла бы сдвинуть сложность, и экземпляры одного шаблона перестали бы быть
сопоставимы. Seed хранится в tasks.seed, поэтому экземпляр воспроизводим.
"""

from __future__ import annotations

import random
from typing import Any

from taskgen.bank.parser import PLACEHOLDER
from taskgen.contracts import ParamSpec


def draw_params(specs: dict[str, ParamSpec], rng: random.Random) -> dict[str, Any]:
    """Значения параметров: int — целое из [min, max], float — равномерно, choice — из choices."""
    values: dict[str, Any] = {}
    # Порядок по имени: перестановка параметров во frontmatter не меняет экземпляр по seed.
    for name in sorted(specs):
        spec = specs[name]
        if spec.type == "int":
            values[name] = rng.randint(int(spec.min), int(spec.max))
        elif spec.type == "float":
            values[name] = rng.uniform(spec.min, spec.max)
        else:
            values[name] = rng.choice(spec.choices)
    return values


def fill_placeholders(text: str, params: dict[str, Any]) -> str:
    """Подставляет {name} из params; {{ и }} превращаются в одиночные скобки.

    Подстановки ищет то же выражение, которым разбор шаблона проверяет их при импорте.
    """
    filled = PLACEHOLDER.sub(
        lambda match: str(params[match.group(1)]) if match.group(1) in params else match.group(0),
        text,
    )
    return filled.replace("{{", "{").replace("}}", "}")
