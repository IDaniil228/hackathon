#!/usr/bin/env python3
"""Проверка контраста WCAG 2.1 для пар "текст/фон" из brand/contrast-pairs.json.

Пары проверяются в обеих темах. Код выхода 1, если хоть одна пара ниже порога.
Запуск: python3 brand/check_contrast.py
"""

import json
import re
import sys
from collections import namedtuple
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build  # noqa: E402

PAIRS_PATH = HERE / "contrast-pairs.json"
# Пороги WCAG 2.1: 1.4.3 для обычного и крупного текста, 1.4.11 для границ элементов управления
THRESHOLDS = {"text": 4.5, "large-text": 3.0, "ui": 3.0}

Result = namedtuple("Result", "theme fg bg kind where ratio required ok")


def linear(channel):
    c = channel / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb):
    r, g, b = (linear(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def composite(fg, bg):
    """Накладывает полупрозрачный цвет на непрозрачный фон."""
    alpha = fg[3]
    return tuple(round(f * alpha + b * (1 - alpha)) for f, b in zip(fg[:3], bg[:3])) + (1.0,)


def contrast_ratio(fg, bg):
    if fg[3] < 1:
        fg = composite(fg, bg)
    lighter, darker = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def load_pairs(path=PAIRS_PATH):
    return json.loads(path.read_text(encoding="utf-8"))["pairs"]


def evaluate(tokens, pairs):
    results = []
    for theme in build.THEMES:
        for pair in pairs:
            fg = tokens.color(f"theme.{theme}.{pair['fg']}")
            bg = tokens.color(f"theme.{theme}.{pair['bg']}")
            if bg[3] < 1:
                raise build.TokenError(f"фон {pair['bg']} полупрозрачен: контраст не определён")
            ratio = contrast_ratio(fg, bg)
            required = THRESHOLDS[pair["kind"]]
            results.append(Result(theme, pair["fg"], pair["bg"], pair["kind"], pair["where"], ratio, required, ratio >= required))
    return results


def rule_blocks(css):
    """Пары (селектор, тело правила) без комментариев и вложенных @-блоков."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [(m.group(1).strip(), m.group(2)) for m in re.finditer(r"([^{}@]+)\{([^{}]*)\}", css)]


def var_of(body, prop):
    """Имя семантического цвета, если свойство задано как var(--fsp-color-X)."""
    m = re.search(rf"(?:^|;|\s){re.escape(prop)}\s*:\s*var\(--{build.PREFIX}-color-([a-z0-9-]+)\)", body)
    return m.group(1) if m else None


def main():
    tokens = build.Tokens(json.loads(build.TOKENS_PATH.read_text(encoding="utf-8")))
    results = evaluate(tokens, load_pairs())
    for r in results:
        mark = "ok  " if r.ok else "FAIL"
        print(f"{mark} {r.theme:5} {r.ratio:5.2f} >= {r.required:3.1f}  {r.fg} на {r.bg}  ({r.kind}; {r.where})")
    failed = [r for r in results if not r.ok]
    print(f"\nпар: {len(results)}, ниже порога: {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
