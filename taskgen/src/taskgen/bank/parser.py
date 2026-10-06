"""Разбор md-шаблона в TemplateSpec. Формат описан в taskgen/TEMPLATE_FORMAT.md.

Разбор собирает все найденные проблемы, а не останавливается на первой: автор шаблона
исправляет файл за один проход. Справочники (области, грейды, навыки) здесь не проверяются,
для этого нужна БД — проверка идёт при импорте.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

import yaml
from pydantic import ValidationError

from taskgen.config import ScoringSettings
from taskgen.contracts import RubricItem, TaskKind, TemplateSpec
from taskgen.db import Template

SECTION_GENERATION = "Генерация"
SECTION_QUESTIONS = "Вопросы"
SECTION_FACTS = "Факты"
SECTION_FORBIDDEN = "Нельзя раскрывать"
SECTION_REFERENCE = "Эталон"
SECTION_SIGNATURE = "Сигнатура"
SECTION_TESTS = "Тесты"
SECTION_RUBRIC = "Рубрика"
ESSAY_ONLY_SECTIONS = (SECTION_QUESTIONS, SECTION_FACTS, SECTION_FORBIDDEN)
CODE_ONLY_SECTIONS = (SECTION_REFERENCE, SECTION_SIGNATURE, SECTION_TESTS)
KNOWN_SECTIONS = (SECTION_GENERATION, *ESSAY_ONLY_SECTIONS, *CODE_ONLY_SECTIONS, SECTION_RUBRIC)

# Поля TemplateSpec, которые берутся из разделов тела; остальные задаёт frontmatter.
BODY_FIELDS = frozenset(
    {
        "generator_prompt",
        "entrypoint",
        "reference_solution",
        "test_generator",
        "questions",
        "scenario_facts",
        "forbidden",
        "rubric",
    }
)
FRONTMATTER_FIELDS = frozenset(TemplateSpec.model_fields) - BODY_FIELDS
SCORING_WEIGHTS = frozenset(k for k in ScoringSettings.model_fields if k.startswith("weight_"))

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# {{n}} — экранированная скобка, а не подстановка, поэтому двойные скобки не считаются.
PLACEHOLDER = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})")
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")
SECTION_HEADING = re.compile(r"^##\s+(.+?)\s*#*\s*$")
TITLE_HEADING = re.compile(r"^#\s+\S")
RUBRIC_ITEM = re.compile(r"^-\s+([A-Za-z0-9_.-]+):\s+(\S.*)$")
RUBRIC_EXAMPLE = re.compile(r"^\s+([+-])\s+(\S.*)$")
FORBIDDEN_ITEM = re.compile(r"^-\s+(\S.*)$")
# Подчёркивание входит в \w, но в фразах вроде select_related оно разделитель, как дефис.
_NOT_WORD = re.compile(r"[\W_]+")


def normalize_phrase(text: str) -> str:
    """Текст для поиска запрещённых фраз: регистр, ё/е, пунктуация и пробелы не различаются."""
    return " ".join(_NOT_WORD.sub(" ", text.casefold().replace("ё", "е")).split())


def find_phrases(text: str, phrases: list[str]) -> list[str]:
    """Фразы, которые входят в текст подстрокой после нормализации, в исходном написании.

    Словоформы не сводятся к основе: автор шаблона пишет общую часть слова, например
    "утечк", и она найдётся в "утечка" и "утечки", но и внутри более длинных слов.
    """
    haystack = normalize_phrase(text)
    return [phrase for phrase in phrases if normalize_phrase(phrase) in haystack]


@dataclass(frozen=True)
class TemplateIssue:
    location: str
    message: str
    source: str | None = None

    def __str__(self) -> str:
        prefix = f"{self.source}: " if self.source else ""
        return f"{prefix}{self.location}: {self.message}"


class TemplateError(Exception):
    """Шаблон или набор шаблонов не прошёл проверку; issues перечисляет все причины."""

    def __init__(self, issues: list[TemplateIssue]) -> None:
        self.issues = list(issues)
        super().__init__("\n".join(str(issue) for issue in self.issues))


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def section_label(name: str) -> str:
    return f"раздел «{name}»"


@dataclass
class _Collector:
    source: str
    issues: list[TemplateIssue] = field(default_factory=list)

    def add(self, location: str, message: str) -> None:
        self.issues.append(TemplateIssue(location, message, self.source))


@dataclass
class _CodeBlock:
    info: str
    content: str


def parse_template(text: str, *, source: str) -> TemplateSpec:
    """Разбирает md-шаблон; при любой проблеме бросает TemplateError со списком всех причин."""
    out = _Collector(source)
    text = text.removeprefix("﻿")
    split = _split_frontmatter(text, out)
    if split is None:
        raise TemplateError(out.issues)
    meta, body = split

    sections = _split_sections(body, out)
    kind = meta.get("kind")
    is_code = kind == TaskKind.CODE.value
    _check_frontmatter(meta, is_code=is_code, out=out)

    data: dict[str, Any] = {k: v for k, v in meta.items() if k in FRONTMATTER_FIELDS}
    params = meta.get("params") if isinstance(meta.get("params"), dict) else {}
    data["generator_prompt"] = _generation(sections, params, out)
    data["rubric"] = _rubric(sections, out)
    if is_code:
        data.update(_code_sections(sections, meta.get("runtime"), out))
        for name in ESSAY_ONLY_SECTIONS:
            if name in sections:
                out.add(section_label(name), "раздел задаётся только заданиям kind=essay")
    elif kind == TaskKind.ESSAY.value:
        data.update(_essay_sections(sections, params, out))
        for name in CODE_ONLY_SECTIONS:
            if name in sections:
                out.add(section_label(name), "раздел задаётся только заданиям kind=code")

    try:
        spec = TemplateSpec.model_validate(data)
    except ValidationError as error:
        for item in error.errors():
            out.add(".".join(str(part) for part in item["loc"]), _pydantic_message(item))
        spec = None
    if out.issues or spec is None:
        raise TemplateError(out.issues)
    return spec


def _split_frontmatter(text: str, out: _Collector) -> tuple[dict[str, Any], str] | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        out.add("frontmatter", "файл должен начинаться со строки --- и YAML-заголовка")
        return None
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        out.add("frontmatter", "нет закрывающей строки --- после YAML-заголовка")
        return None
    try:
        meta = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = f" (строка {mark.line + 2})" if mark is not None else ""
        out.add("frontmatter", f"некорректный YAML{where}: {getattr(error, 'problem', error)}")
        return None
    if not isinstance(meta, dict):
        out.add("frontmatter", "YAML-заголовок должен быть словарём поле: значение")
        return None
    return meta, "\n".join(lines[end + 1 :])


def _split_sections(body: str, out: _Collector) -> dict[str, str]:
    """Режет тело по заголовкам ##, пропуская заголовки внутри блоков кода."""
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    fence: str | None = None
    stray = False
    for line in body.splitlines():
        match = FENCE.match(line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not match.group(2).strip():
                fence = None
        elif fence is None and (heading := SECTION_HEADING.match(line)):
            name = heading.group(1)
            if name not in KNOWN_SECTIONS:
                known = ", ".join(KNOWN_SECTIONS)
                out.add(section_label(name), f"неизвестный раздел; допустимы: {known}")
            elif name in sections:
                out.add(section_label(name), "раздел встречается больше одного раза")
            current = sections.setdefault(name, [])
            continue
        if current is not None:
            current.append(line)
        elif line.strip() and not TITLE_HEADING.match(line):
            stray = True
    if stray:
        out.add("тело", "текст до первого раздела ## не относится ни к одному разделу")
    if fence is not None:
        out.add("тело", "блок кода не закрыт")
    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


def _check_frontmatter(meta: dict[str, Any], *, is_code: bool, out: _Collector) -> None:
    for key in meta:
        if key not in FRONTMATTER_FIELDS:
            out.add(str(key), "неизвестное поле frontmatter")

    description = meta.get("description")
    if isinstance(description, str) and not description.strip():
        out.add("description", "описание не может быть пустым: по нему считается эмбеддинг")

    name = meta.get("name")
    name_limit = Template.__table__.c.name.type.length
    if isinstance(name, str) and len(name) > name_limit:
        out.add("name", f"имя длиннее {name_limit} символов")

    skills = meta.get("skills")
    if isinstance(skills, list):
        if not skills:
            out.add("skills", "нужен хотя бы один навык")
        if all(isinstance(s, str) for s in skills) and len(set(skills)) != len(skills):
            out.add("skills", "навыки повторяются")

    runtime = meta.get("runtime")
    if is_code and not runtime:
        out.add("runtime", "заданию с кодом нужно окружение песочницы")
    if meta.get("kind") == TaskKind.ESSAY.value and runtime is not None:
        out.add("runtime", "окружение песочницы задаётся только заданиям kind=code")
    runtime_limit = Template.__table__.c.runtime.type.length
    if isinstance(runtime, str) and len(runtime) > runtime_limit:
        out.add("runtime", f"имя окружения длиннее {runtime_limit} символов")

    params = meta.get("params")
    if isinstance(params, dict):
        for param, spec in params.items():
            _check_param(f"params.{param}", spec, out)

    scoring = meta.get("scoring")
    if isinstance(scoring, dict):
        for key, value in scoring.items():
            if key not in SCORING_WEIGHTS:
                allowed = ", ".join(sorted(SCORING_WEIGHTS))
                out.add(f"scoring.{key}", f"неизвестный вес; допустимы: {allowed}")
            elif isinstance(value, int | float) and not isinstance(value, bool) and value < 0:
                out.add(f"scoring.{key}", "вес не может быть отрицательным")


def _check_param(location: str, spec: Any, out: _Collector) -> None:
    if not isinstance(spec, dict):
        return  # Тип проверит pydantic и назовёт то же место.
    kind = spec.get("type")
    low, high = spec.get("min"), spec.get("max")
    if kind in ("int", "float"):
        if low is None or high is None:
            out.add(location, "числовому параметру нужны min и max")
        elif _is_number(low) and _is_number(high) and low > high:
            out.add(location, "min больше max")
        if kind == "int" and any(isinstance(v, float) and not v.is_integer() for v in (low, high)):
            out.add(location, "границы целочисленного параметра должны быть целыми")
        if spec.get("choices") is not None:
            out.add(location, "choices задаются только параметру type: choice")
    elif kind == "choice":
        choices = spec.get("choices")
        if not isinstance(choices, list) or not choices:
            out.add(location, "параметру type: choice нужен непустой список choices")
        if low is not None or high is not None:
            out.add(location, "min и max задаются только числовым параметрам")


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _generation(sections: dict[str, str], params: dict[str, Any], out: _Collector) -> str:
    text = sections.get(SECTION_GENERATION)
    label = section_label(SECTION_GENERATION)
    if text is None:
        out.add(label, "раздел обязателен")
        return ""
    if not text:
        out.add(label, "раздел пуст")
    _check_placeholders(label, text, params, out)
    return text


def _check_placeholders(label: str, text: str, params: dict[str, Any], out: _Collector) -> None:
    unknown = sorted(set(PLACEHOLDER.findall(text)) - set(params))
    if unknown:
        names = ", ".join(f"{{{name}}}" for name in unknown)
        out.add(label, f"подстановки без параметра в frontmatter: {names}")


def _essay_sections(
    sections: dict[str, str], params: dict[str, Any], out: _Collector
) -> dict[str, Any]:
    questions = sections.get(SECTION_QUESTIONS)
    questions_label = section_label(SECTION_QUESTIONS)
    if questions is None:
        out.add(questions_label, "раздел обязателен для заданий kind=essay")
    elif not questions:
        out.add(questions_label, "раздел пуст")
    else:
        _check_placeholders(questions_label, questions, params, out)

    facts = _facts(sections, params, out)
    forbidden = _forbidden(sections, out)

    # Вопрос с запрещённой фразой сам раскрывает ответ. Факт с ней обязан попасть в
    # ситуацию, но проверка ситуации его отклонит: шаблон не даст ни одного экземпляра.
    # По той же причине проверяются значения параметров, которые попадают в текст.
    if questions:
        for phrase in find_phrases(questions, forbidden):
            out.add(questions_label, f"вопросы содержат запрещённую фразу {phrase!r}")
    for fact in facts:
        for phrase in find_phrases(fact["text"], forbidden):
            out.add(
                section_label(SECTION_FACTS),
                f"факт {fact['id']!r} содержит запрещённую фразу {phrase!r}",
            )
    for name, spec in params.items():
        choices = spec.get("choices") if isinstance(spec, dict) else None
        for choice in choices if isinstance(choices, list) else []:
            for phrase in find_phrases(str(choice), forbidden):
                out.add(
                    f"params.{name}", f"значение {choice!r} содержит запрещённую фразу {phrase!r}"
                )
    return {"questions": questions, "scenario_facts": facts, "forbidden": forbidden}


def _facts(
    sections: dict[str, str], params: dict[str, Any], out: _Collector
) -> list[dict[str, str]]:
    text = sections.get(SECTION_FACTS)
    label = section_label(SECTION_FACTS)
    if text is None:
        out.add(label, "раздел обязателен для заданий kind=essay")
        return []
    facts: list[dict[str, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if item := RUBRIC_ITEM.match(line):
            facts.append({"id": item.group(1), "text": item.group(2).strip()})
        else:
            out.add(label, f"строка {number}: ожидается факт '- id: текст'")
    if not facts:
        out.add(label, "нужен хотя бы один факт '- id: текст'")
    ids = [fact["id"] for fact in facts]
    for duplicate in sorted({i for i in ids if ids.count(i) > 1}):
        out.add(label, f"идентификатор факта {duplicate!r} повторяется")
    _check_placeholders(label, text, params, out)
    return facts


def _forbidden(sections: dict[str, str], out: _Collector) -> list[str]:
    text = sections.get(SECTION_FORBIDDEN)
    if text is None:
        return []
    label = section_label(SECTION_FORBIDDEN)
    phrases: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        item = FORBIDDEN_ITEM.match(line)
        if item is None:
            out.add(label, f"строка {number}: ожидается фраза '- текст'")
        elif not normalize_phrase(item.group(1)):
            # Пустая после нормализации фраза входит в любой текст и отклонит каждую ситуацию.
            out.add(label, f"строка {number}: во фразе нет ни букв, ни цифр")
        else:
            phrases.append(item.group(1).strip())
    if not phrases:
        out.add(label, "нужна хотя бы одна фраза '- текст'; если фраз нет, уберите раздел")
    return phrases


def _rubric(sections: dict[str, str], out: _Collector) -> list[RubricItem]:
    text = sections.get(SECTION_RUBRIC)
    label = section_label(SECTION_RUBRIC)
    if text is None:
        out.add(label, "раздел обязателен")
        return []
    items: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if item := RUBRIC_ITEM.match(line):
            items.append({"id": item.group(1), "text": item.group(2).strip()})
            continue
        example = RUBRIC_EXAMPLE.match(line)
        if example and items:
            key = "passing_example" if example.group(1) == "+" else "failing_example"
            if key in items[-1]:
                out.add(label, f"строка {number}: у пункта уже есть пример этого вида")
            items[-1][key] = example.group(2).strip()
            continue
        out.add(
            label,
            f"строка {number}: ожидается пункт '- id: текст' или вложенный пример "
            "'+ проходит' / '- не проходит'",
        )
    if not items:
        out.add(label, "нужен хотя бы один пункт '- id: текст'")
    ids = [item["id"] for item in items]
    for duplicate in sorted({i for i in ids if ids.count(i) > 1}):
        out.add(label, f"идентификатор пункта {duplicate!r} повторяется")
    return [RubricItem(**item) for item in items]


def _code_sections(sections: dict[str, str], runtime: Any, out: _Collector) -> dict[str, Any]:
    result: dict[str, Any] = {}

    signature = _single_block(sections, SECTION_SIGNATURE, out)
    entrypoint = None
    if signature is not None:
        line = signature.content.strip()
        if "\n" in line or not IDENTIFIER.match(line):
            out.add(
                section_label(SECTION_SIGNATURE),
                "блок должен содержать одну строку — имя функции из латиницы, цифр и _",
            )
        else:
            entrypoint = line
    result["entrypoint"] = entrypoint

    reference = _single_block(sections, SECTION_REFERENCE, out)
    if reference is not None:
        result["reference_solution"] = reference.content
        label = section_label(SECTION_REFERENCE)
        if runtime == "python":
            tree = _parse_python(reference.content, label, out)
            if tree is not None and entrypoint and entrypoint not in _top_functions(tree):
                out.add(label, f"нет функции верхнего уровня {entrypoint!r} из раздела Сигнатура")
        elif entrypoint and not re.search(rf"\b{re.escape(entrypoint)}\b", reference.content):
            out.add(label, f"эталон не упоминает функцию {entrypoint!r} из раздела Сигнатура")

    tests = _single_block(sections, SECTION_TESTS, out)
    if tests is not None:
        result["test_generator"] = tests.content
        label = section_label(SECTION_TESTS)
        tree = _parse_python(tests.content, label, out)
        if tree is not None:
            generate = _top_functions(tree).get("generate")
            if generate is None or len(generate.args.args) < 2:
                out.add(label, "нужна функция верхнего уровня generate(params, rng)")
    return result


def _single_block(sections: dict[str, str], name: str, out: _Collector) -> _CodeBlock | None:
    label = section_label(name)
    text = sections.get(name)
    if text is None:
        out.add(label, "раздел обязателен для заданий kind=code")
        return None
    blocks = _code_blocks(text)
    if len(blocks) != 1:
        out.add(label, f"нужен ровно один блок кода, найдено {len(blocks)}")
        return None
    if not blocks[0].content.strip():
        out.add(label, "блок кода пуст")
        return None
    return blocks[0]


def _code_blocks(text: str) -> list[_CodeBlock]:
    blocks: list[_CodeBlock] = []
    fence: str | None = None
    info = ""
    lines: list[str] = []
    for line in text.splitlines():
        match = FENCE.match(line)
        if fence is None and match:
            fence, info, lines = match.group(1), match.group(2).strip(), []
        elif (
            fence is not None
            and match
            and match.group(1)[0] == fence[0]
            and len(match.group(1)) >= len(fence)
            and not match.group(2).strip()
        ):
            blocks.append(_CodeBlock(info, "\n".join(lines)))
            fence = None
        elif fence is not None:
            lines.append(line)
    return blocks


def _parse_python(code: str, label: str, out: _Collector) -> ast.Module | None:
    try:
        return ast.parse(code)
    except SyntaxError as error:
        out.add(label, f"синтаксическая ошибка Python в строке {error.lineno}: {error.msg}")
        return None


def _top_functions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


_PYDANTIC_MESSAGES = {
    "missing": "обязательное поле не задано",
    "string_type": "ожидается строка",
    "int_type": "ожидается целое число",
    "int_parsing": "ожидается целое число",
    "int_from_float": "ожидается целое число",
    "float_type": "ожидается число",
    "float_parsing": "ожидается число",
    "list_type": "ожидается список",
    "dict_type": "ожидается словарь",
    "enum": "допустимые значения: {expected}",
    "literal_error": "допустимые значения: {expected}",
    "greater_than_equal": "значение должно быть не меньше {ge}",
    "less_than_equal": "значение должно быть не больше {le}",
    "string_pattern_mismatch": "значение не подходит под шаблон {pattern}",
}


def _pydantic_message(error: Any) -> str:
    template = _PYDANTIC_MESSAGES.get(error["type"])
    if template is None:
        return f"некорректное значение ({error['msg']})"
    ctx = dict(error.get("ctx") or {})
    if isinstance(ctx.get("expected"), str):
        ctx["expected"] = ctx["expected"].replace(" or ", ", ")
    return template.format_map(ctx)
