"""Проверки текста от LLM-писателя, которым не нужен смысл текста.

Текст — заголовок и условие задания с кодом, ситуация эссе или кейс задачки. Каждая проверка
возвращает причину отказа для писателя, и отказ уходит в тот же цикл переписывания, что и
отказ по утечке тестов. Ложный отказ честного текста хуже пропуска: он тратит попытку и может
оставить шаблон без экземпляра. Поэтому проверка срабатывает только на признак, которого
в честном тексте нет.
"""

from __future__ import annotations

import re

from taskgen.bank.parser import FENCE, closes_fence, unclosed_fence

# Управляющие символы, кроме табуляции и переводов строк. В Markdown им не место, а \b и \f
# получаются из LaTeX-команд вроде \beta и \frac, записанных в JSON одним обратным слэшем.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MATH = re.compile(r"\$\$(.+?)\$\$|\$([^$]+?)\$", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")
# Внутри формулы JSON превращает \t, \r и \n в табуляцию, возврат каретки и перевод строки.
# Табуляции и возврату каретки в формуле не место. Перевод строки в формуле $$…$$ честный,
# поэтому дефектом считается только перевод строки, за которым идёт остаток команды на n.
# Короткие команды \nu, \ne и \ni не учитываются: остаток u, e или i похож на переменную.
_SPOILED_IN_MATH = re.compile(
    r"[\t\r]|\n(?:abla|eq|eg|ewline|otin|ot|exists|leq|geq|mid|subseteq)(?![A-Za-z])"
)

SPOILED_ESCAPES = (
    "в тексте испорчены LaTeX-команды: в JSON обратный слэш перед буквой стал управляющим "
    "символом, например \\frac стал переводом страницы и «rac». Удвой обратные слэши в "
    "формулах: в JSON-строке пиши \\\\frac, \\\\beta, \\\\theta"
)


def _prose(text: str) -> str:
    """Текст без блоков и фрагментов кода: в коде $, табуляция и переводы строк честные."""
    lines: list[str] = []
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        match = FENCE.match(line)
        if fence is not None:
            if match is not None and closes_fence(fence, match):
                fence = None
        elif match is not None:
            fence = match.group(1)
        else:
            lines.append(_INLINE_CODE.sub(" ", line))
    return "".join(lines)


def spoiled_escapes(text: str) -> bool:
    """Испорчены ли LaTeX-команды чтением JSON с одним обратным слэшем."""
    if _CONTROL.search(text):
        return True
    return any(
        _SPOILED_IN_MATH.search(match.group(1) or match.group(2))
        for match in _MATH.finditer(_prose(text))
    )


def text_problems(text: str) -> list[str]:
    """Причины отказа для писателя; пустой список — дефектов нет."""
    problems = []
    if unclosed_fence(text):
        problems.append("текст оборвался посреди блока кода; допиши его целиком и закрой блок")
    if spoiled_escapes(text):
        problems.append(SPOILED_ESCAPES)
    return problems
