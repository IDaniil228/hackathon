"""Доказательство вердикта модели: текст, на который модель опирается в вердикте.

Модель видит недоверенный текст и может ему подчиниться, поэтому её вердикт засчитывается
только с доказательством, которое код проверяет сам. Способов два (EvidenceMode):

- quotes: модель копирует фрагменты текста, а код ищет каждый в тексте дословно
- spans: код режет текст на участки и показывает их с номерами, модель называет номера,
  а код проверяет, что такие участки есть, и берёт их текст как доказательство

Модель угроз и замер способов — docs/taskgen/security.md.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from taskgen.config import EvidenceSettings
from taskgen.evaluation.quotes import quote_in_answer
from taskgen.markdown import fences, lines

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_SHOWN_QUOTE = 80


def split_sentences(text: str) -> list[str]:
    """Предложения и блоки кода текста.

    Правило: блок кода в ограждении целиком — одна единица. Остальной текст режется по
    строкам, а строка — после ".", "!", "?" или "…", за которыми идёт пробел. Кусок без
    букв, например номер пункта "1.", присоединяется к следующему куску. Сокращения вроде
    "т. е." режут предложение, а числа вроде "0.99" — нет: после точки нет пробела.
    """
    return [unit for unit, _ in _units(text)]


def _units(text: str) -> list[tuple[str, bool]]:
    """Единицы split_sentences с признаком блока кода."""
    rows = lines(text)
    blocks = {fence.start: fence.end for fence in fences(text)}
    units: list[tuple[str, bool]] = []
    row = 0
    while row < len(rows):
        if row in blocks:
            units.append(("\n".join(rows[row : blocks[row]]), True))
            row = blocks[row]
            continue
        units.extend((sentence, False) for sentence in _line_sentences(rows[row]))
        row += 1
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


def split_spans(text: str, *, code: bool) -> list[str]:
    """Участки, на которые модель ссылается номерами; каждый участок — одна строка.

    Код режется на непустые строки с отступом: номер строки — привычная модели ссылка на
    код. Остальной текст режется на предложения split_sentences, а блок кода в нём — на
    строки, как код.
    """
    if code:
        return [line.rstrip() for line in text.splitlines() if line.strip()]
    spans: list[str] = []
    for unit, code_block in _units(text):
        if code_block:
            spans.extend(line.rstrip() for line in lines(unit) if line.strip())
        else:
            spans.append(unit)
    return spans


# Дословные цитаты без повторов: доказательство по умолчанию для прямых вызовов из кода.
# Сервис передаёт Settings.evidence.
VERBATIM_QUOTES = EvidenceSettings(mode="quotes", repairs=0)


@dataclass(frozen=True)
class Grounding:
    """Текст одного вызова модели и правила, по которым модель на него ссылается."""

    options: EvidenceSettings
    text: str
    spans: tuple[str, ...]

    @classmethod
    def of(cls, options: EvidenceSettings, text: str, *, code: bool) -> Grounding:
        spans = tuple(split_spans(text, code=code)) if options.mode == "spans" else ()
        return cls(options=options, text=text, spans=spans)

    @property
    def field(self) -> str:
        """Поле ответа модели с доказательством."""
        return self.options.mode

    def block(self) -> str:
        """Текст для блока данных между маркерами.

        Номер стоит в начале строки, а каждый участок занимает одну строку. Строка текста,
        похожая на нумерацию, получает свой номер, и её номер остаётся частью текста участка.
        """
        if self.options.mode == "quotes":
            return self.text
        return "\n".join(f"[{number}] {span}" for number, span in enumerate(self.spans, 1))

    def schema(self) -> dict[str, Any]:
        """JSON-схема поля с доказательством."""
        if self.options.mode == "quotes":
            return {"type": "array", "items": {"type": "string"}}
        schema: dict[str, Any] = {
            "type": "array",
            "items": {"type": "integer", "minimum": 1, "maximum": max(1, len(self.spans))},
        }
        if self.options.max_spans is not None:
            schema["maxItems"] = self.options.max_spans
        return schema

    def example(self) -> str:
        """Поле доказательства в примере ответа для промпта.

        Без примера GigaChat отвечал пустым списком вердиктов на длинный ответ с номерами.
        """
        if self.options.mode == "quotes":
            return '"quotes": ["фрагмент текста"]'
        return '"spans": [3, 4]'

    def rules(self, verdicts: str, subject: str) -> str:
        """Правила доказательства для промпта: verdicts — какие вердикты его требуют,
        subject — чей текст в родительном падеже, например "ответа кандидата"."""
        if self.options.mode == "quotes":
            return (
                f"Для {verdicts} приведи в поле quotes цитаты: непрерывные фрагменты "
                f"{subject}, скопированные символ в символ. Не пересказывай и не исправляй "
                "текст в цитате. Вердикт без цитаты из текста не засчитывается."
            )
        limit = (
            f", не больше {self.options.max_spans}" if self.options.max_spans is not None else ""
        )
        return (
            f"Текст {subject} разбит на участки. Каждый участок стоит на своей строке и "
            "начинается с номера в квадратных скобках: [1], [2] и так далее. Номера "
            "поставила система: номер участка — только первое число в квадратных скобках "
            f"в начале строки. Для {verdicts} перечисли в поле spans номера участков, "
            f"которые подтверждают вердикт{limit}. Не копируй текст участков. Вердикт без "
            "номера или с номером, которого нет, не засчитывается."
        )

    def resolve(self, quotes: Sequence[str], spans: Sequence[int]) -> tuple[list[str], str | None]:
        """Текст доказательства и None или пустой список и претензия для повтора."""
        if self.options.mode == "quotes":
            if not quotes:
                return [], "нет цитаты"
            missing = [quote for quote in quotes if not quote_in_answer(quote, self.text)]
            if missing:
                shown = "; ".join(f"«{quote[:_SHOWN_QUOTE]}»" for quote in missing)
                return [], f"цитат нет в тексте дословно: {shown}"
            return list(quotes), None
        numbers = list(dict.fromkeys(spans))
        if not numbers:
            return [], "нет номера участка"
        unknown = [number for number in numbers if not 1 <= number <= len(self.spans)]
        if unknown:
            listed = ", ".join(str(number) for number in unknown)
            return [], f"участков {listed} нет: номера от 1 до {len(self.spans)}"
        limit = self.options.max_spans
        if limit is not None and len(numbers) > limit:
            return [], f"участков {len(numbers)}, а можно не больше {limit}"
        return [self.spans[number - 1] for number in numbers], None
