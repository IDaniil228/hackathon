# Копия taskgen/src/taskgen/markdown.py от api/scripts/sync_llm.py, не править
"""Разметка Markdown в шаблонах и текстах моделей: блоки кода, заголовки, frontmatter.

Разбор делает markdown-it-py по правилам CommonMark, поэтому сервис видит тот же блок кода
или заголовок, что и читатель отрисованного текста. Свой формат поверх Markdown, например
'### id +' или '- id: текст', разбирают модули, которым он принадлежит.

Номера строк считаются по lines(text): markdown-it режет текст только по переводу строки,
а str.splitlines режет ещё по \\f и другим символам, которые в тексте модели бывают.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.front_matter import front_matter_plugin

_MD = MarkdownIt("commonmark").use(front_matter_plugin)
_NEWLINE = re.compile(r"\r\n?")
# Место после конца строки: после \n и после \r, за которым нет \n.
_LINE_END = re.compile(r"(?<=\n)|(?<=\r)(?!\n)")


@dataclass(frozen=True)
class Fence:
    """Блок кода в заборе; start и end — строки [start, end) по lines(text), заборы входят."""

    info: str
    content: str
    start: int
    end: int
    closed: bool


@dataclass(frozen=True)
class Heading:
    level: int
    title: str
    line: int


def lines(text: str, *, keepends: bool = False) -> list[str]:
    """Строки текста так, как их нумерует markdown-it.

    keepends оставляет перевод строки в том виде, в каком он был: \\r в формуле — след
    испорченной LaTeX-команды, и проверка текста должна его видеть.
    """
    if keepends:
        return _LINE_END.split(text)
    return _NEWLINE.sub("\n", text).split("\n")


def _tokens(text: str) -> list[Token]:
    return _MD.parse(text)


def fences(text: str) -> list[Fence]:
    """Все блоки кода в заборах, в том числе внутри списков и цитат."""
    rows = lines(text)
    found = []
    for token in _tokens(text):
        if token.type != "fence" or token.map is None:
            continue
        start, end = token.map
        found.append(
            Fence(
                info=token.info.strip(),
                content=token.content.removesuffix("\n"),
                start=start,
                end=end,
                closed=_closes(rows[end - 1], token.markup) if end - start > 1 else False,
            )
        )
    return found


def _closes(line: str, markup: str) -> bool:
    # markdown-it не отмечает незакрытый блок: он просто идёт до конца текста. Блок закрыт,
    # если его последняя строка — забор того же символа и не короче открывающего.
    marker = line.lstrip(" \t>").rstrip()
    return bool(marker) and set(marker) == {markup[0]} and len(marker) >= len(markup)


def unclosed_fence(text: str) -> bool:
    """Остался ли в тексте открытый блок кода: признак текста, оборванного посреди кода."""
    return any(not fence.closed for fence in fences(text))


def code_rows(text: str) -> set[int]:
    """Номера строк lines(text), которые входят в блоки кода вместе с заборами."""
    return {row for fence in fences(text) for row in range(fence.start, fence.end)}


def headings(text: str) -> list[Heading]:
    """Заголовки верхнего уровня документа; заголовки внутри списков и цитат не входят."""
    tokens = _tokens(text)
    return [
        Heading(level=int(token.tag[1:]), title=tokens[i + 1].content, line=token.map[0])
        for i, token in enumerate(tokens)
        if token.type == "heading_open" and token.level == 0 and token.map is not None
    ]


def split_frontmatter(text: str) -> tuple[str, str] | None:
    """YAML-заголовок между строками --- и тело после него; None — заголовка нет."""
    for token in _tokens(text):
        if token.type == "front_matter" and token.map is not None:
            return token.content, "\n".join(lines(text)[token.map[1] :])
    return None
