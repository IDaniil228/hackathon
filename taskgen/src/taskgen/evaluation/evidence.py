"""Доказательство вердикта модели: текст, на который модель опирается в вердикте.

Модель видит недоверенный текст и может ему подчиниться, поэтому её вердикт засчитывается
только с доказательством, которое код проверяет сам. Модель угроз — docs/taskgen/security.md.
"""

from __future__ import annotations

import re

from taskgen.bank.parser import FENCE, closes_fence

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def split_sentences(text: str) -> list[str]:
    """Предложения и блоки кода текста.

    Правило: блок кода в ограждении целиком — одна единица. Остальной текст режется по
    строкам, а строка — после ".", "!", "?" или "…", за которыми идёт пробел. Кусок без
    букв, например номер пункта "1.", присоединяется к следующему куску. Сокращения вроде
    "т. е." режут предложение, а числа вроде "0.99" — нет: после точки нет пробела.
    """
    units: list[str] = []
    block: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        match = FENCE.match(line)
        if fence is not None:
            block.append(line)
            if match is not None and closes_fence(fence, match):
                units.append("\n".join(block))
                block, fence = [], None
        elif match is not None:
            block, fence = [line], match.group(1)
        else:
            units.extend(_line_sentences(line))
    if block:
        units.append("\n".join(block))
    return units


def _line_sentences(line: str) -> list[str]:
    sentences: list[str] = []
    carry = ""
    for piece in _SENTENCE_END.split(line.strip()):
        if not piece:
            continue
        joined = f"{carry} {piece}" if carry else piece
        if any(char.isalpha() for char in piece):
            sentences.append(joined)
            carry = ""
        else:
            carry = joined
    if carry:
        if sentences:
            sentences[-1] = f"{sentences[-1]} {carry}"
        else:
            sentences.append(carry)
    return sentences
