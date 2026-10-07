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
from typing import Any, get_args

import yaml
from pydantic import ValidationError

from taskgen.contracts import CheckMode, IOKind, RubricItem, TaskKind, TemplateSpec
from taskgen.db import Template
from taskgen.evaluation.languages import (
    canonical_language,
    function_runtimes,
    language_runtimes,
    source_language,
)
from taskgen.evaluation.scoring import WEIGHT_OF
from taskgen.markdown import Fence, code_rows, fences, headings, lines, split_frontmatter

SECTION_GENERATION = "Генерация"
SECTION_QUESTIONS = "Вопросы"
SECTION_FACTS = "Факты"
SECTION_FORBIDDEN = "Нельзя раскрывать"
SECTION_SOLUTIONS = "Решения"
SECTION_SIGNATURE = "Сигнатура"
SECTION_TESTS = "Тесты"
SECTION_RUBRIC = "Рубрика"
SECTION_QUESTION = "Вопрос"
SECTION_VARIANTS = "Варианты"
SECTION_CHECKER = "Проверяльщик"
# Разделы, которые задаются только заданиям перечисленных типов; «Генерация» нужна всем.
KIND_SECTIONS: dict[str, frozenset[TaskKind]] = {
    SECTION_SOLUTIONS: frozenset({TaskKind.CODE}),
    SECTION_SIGNATURE: frozenset({TaskKind.CODE}),
    SECTION_TESTS: frozenset({TaskKind.CODE}),
    SECTION_CHECKER: frozenset({TaskKind.CODE}),
    SECTION_QUESTIONS: frozenset({TaskKind.ESSAY}),
    SECTION_FACTS: frozenset({TaskKind.ESSAY}),
    SECTION_QUESTION: frozenset({TaskKind.QUIZ}),
    SECTION_VARIANTS: frozenset({TaskKind.QUIZ}),
    SECTION_FORBIDDEN: frozenset({TaskKind.ESSAY, TaskKind.QUIZ}),
    # Задачка сверяется с ключом варианта, рубрика ей не нужна.
    SECTION_RUBRIC: frozenset({TaskKind.CODE, TaskKind.ESSAY}),
}
KNOWN_SECTIONS = (SECTION_GENERATION, *KIND_SECTIONS)
# Меньше трёх неверных ответов — угадать проще, чем при четырёх вариантах.
MIN_WRONG_ANSWERS = 3
MIN_VARIANTS = 2
# Режим проверки по умолчанию: ответ функции — значение JSON, ответ программы — текст.
DEFAULT_CHECK = {IOKind.FUNCTION: "json", IOKind.STDIO: "tokens"}
# Ответ функции драйвер печатает JSON: текстовые режимы сравнивали бы его запись, а не значение.
FUNCTION_CHECKS = frozenset({"json", "checker"})

# Поля TemplateSpec, которые берутся из разделов тела; остальные задаёт frontmatter.
BODY_FIELDS = frozenset(
    {
        "generator_prompt",
        "entrypoint",
        "solutions",
        "test_generator",
        "questions",
        "scenario_facts",
        "forbidden",
        "question",
        "variants",
        "rubric",
    }
)
FRONTMATTER_FIELDS = frozenset(TemplateSpec.model_fields) - BODY_FIELDS
# Шаблон переопределяет только веса балла задания; вес блока задачек — настройка сессии.
SCORING_WEIGHTS = frozenset(WEIGHT_OF.values())

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# {{n}} — экранированная скобка, а не подстановка, поэтому двойные скобки не считаются.
PLACEHOLDER = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})")
# Уровни заголовков: # — название шаблона, ## — раздел, ### — решение или вариант кейса.
TITLE_LEVEL, SECTION_LEVEL, ENTRY_LEVEL = 1, 2, 3
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
    if kind != TaskKind.QUIZ.value:
        data["rubric"] = _rubric(sections, out)
    if is_code:
        data.update(_code_sections(sections, meta, out))
    elif kind == TaskKind.ESSAY.value:
        data.update(_essay_sections(sections, params, out))
    elif kind == TaskKind.QUIZ.value:
        data.update(_quiz_sections(sections, params, out))
    if kind in set(TaskKind):
        for name, kinds in KIND_SECTIONS.items():
            if name in sections and kind not in kinds:
                allowed = ", ".join(f"kind={k.value}" for k in TaskKind if k in kinds)
                out.add(section_label(name), f"раздел задаётся только заданиям {allowed}")

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
    if lines(text)[0].strip() != "---":
        out.add("frontmatter", "файл должен начинаться со строки --- и YAML-заголовка")
        return None
    split = split_frontmatter(text)
    if split is None:
        out.add("frontmatter", "нет закрывающей строки --- после YAML-заголовка")
        return None
    header, body = split
    try:
        meta = yaml.safe_load(header)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = f" (строка {mark.line + 2})" if mark is not None else ""
        out.add("frontmatter", f"некорректный YAML{where}: {getattr(error, 'problem', error)}")
        return None
    if not isinstance(meta, dict):
        out.add("frontmatter", "YAML-заголовок должен быть словарём поле: значение")
        return None
    return meta, body


def _split_sections(body: str, out: _Collector) -> dict[str, str]:
    """Режет тело по заголовкам ##; до первого раздела допустимо только название #."""
    rows = lines(body)
    outline = headings(body)
    starts = [h for h in outline if h.level == SECTION_LEVEL]
    titles = {h.line for h in outline if h.level == TITLE_LEVEL}
    first = starts[0].line if starts else len(rows)
    if any(row.strip() for i, row in enumerate(rows[:first]) if i not in titles):
        out.add("тело", "текст до первого раздела ## не относится ни к одному разделу")
    if any(not fence.closed for fence in fences(body)):
        out.add("тело", "блок кода не закрыт")

    sections: dict[str, str] = {}
    for heading, end in zip(starts, [h.line for h in starts[1:]] + [len(rows)], strict=True):
        name = heading.title
        if name not in KNOWN_SECTIONS:
            known = ", ".join(KNOWN_SECTIONS)
            out.add(section_label(name), f"неизвестный раздел; допустимы: {known}")
        elif name in sections:
            out.add(section_label(name), "раздел встречается больше одного раза")
        text = "\n".join(rows[heading.line + 1 : end]).strip()
        sections[name] = f"{sections[name]}\n{text}".strip() if name in sections else text
    return sections


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
    if is_code and meta.get("io") is None:
        allowed = ", ".join(io.value for io in IOKind)
        out.add("io", f"заданию с кодом нужен вид io: {allowed}")
    if meta.get("kind") in (TaskKind.ESSAY.value, TaskKind.QUIZ.value):
        if runtime is not None:
            out.add("runtime", "окружение песочницы задаётся только заданиям kind=code")
        for key in ("io", "check"):
            if meta.get(key) is not None:
                out.add(key, "поле задаётся только заданиям kind=code")
    if meta.get("case_groups") and not is_code:
        out.add("case_groups", "группы тестовых кейсов задаются только заданиям kind=code")
    runtime_limit = Template.__table__.c.runtime.type.length
    if isinstance(runtime, str) and len(runtime) > runtime_limit:
        out.add("runtime", f"имя окружения длиннее {runtime_limit} символов")
    if is_code and runtime and runtime not in language_runtimes():
        known = ", ".join(language_runtimes())
        out.add("runtime", f"неизвестное окружение {runtime!r}; допустимы: {known}")
    elif is_code and meta.get("io") == IOKind.FUNCTION.value and runtime not in function_runtimes():
        known = ", ".join(function_runtimes())
        out.add(
            "io",
            f"у окружения {runtime!r} нет драйвера задач-функций, только io=stdio; "
            f"io=function доступно в: {known}",
        )

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
    _check_param_choices(params, forbidden, out)
    return {"questions": questions, "scenario_facts": facts, "forbidden": forbidden}


def _check_param_choices(params: dict[str, Any], forbidden: list[str], out: _Collector) -> None:
    for name, spec in params.items():
        choices = spec.get("choices") if isinstance(spec, dict) else None
        for choice in choices if isinstance(choices, list) else []:
            for phrase in find_phrases(str(choice), forbidden):
                out.add(
                    f"params.{name}", f"значение {choice!r} содержит запрещённую фразу {phrase!r}"
                )


def _quiz_sections(
    sections: dict[str, str], params: dict[str, Any], out: _Collector
) -> dict[str, Any]:
    """Вопрос, варианты кейса и запрещённые фразы тестовой задачки.

    Запрещённые фразы сверяются с вопросом и фактами, но не с ответами: ответы кандидат
    видит списком, а запрет действует на кейс, который не должен назвать верный ответ.
    """
    question = sections.get(SECTION_QUESTION)
    question_label = section_label(SECTION_QUESTION)
    if question is None:
        out.add(question_label, "раздел обязателен для заданий kind=quiz")
    elif not question:
        out.add(question_label, "раздел пуст")
    else:
        _check_placeholders(question_label, question, params, out)

    variants = _variants(sections, params, out)
    forbidden = _forbidden(sections, out)
    if question:
        for phrase in find_phrases(question, forbidden):
            out.add(question_label, f"вопрос содержит запрещённую фразу {phrase!r}")
    for variant in variants:
        for fact in variant["facts"]:
            for phrase in find_phrases(fact["text"], forbidden):
                out.add(
                    section_label(SECTION_VARIANTS),
                    f"факт {fact['id']!r} варианта {variant['id']!r} содержит запрещённую "
                    f"фразу {phrase!r}",
                )
    _check_param_choices(params, forbidden, out)
    return {"question": question, "variants": variants, "forbidden": forbidden}


VARIANT_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_FACTS_LABEL = "Факты:"
_ANSWERS_LABEL = "Ответ:"
ANSWER_ITEM = re.compile(r"^([+-])\s+(\S.*)$")


def _variants(
    sections: dict[str, str], params: dict[str, Any], out: _Collector
) -> list[dict[str, Any]]:
    """Варианты кейса: '### id', затем 'Факты:' со списком '- id: текст' и 'Ответ:' со
    списком '+ верный' и '- неверный'."""
    text = sections.get(SECTION_VARIANTS)
    label = section_label(SECTION_VARIANTS)
    if text is None:
        out.add(label, "раздел обязателен для заданий kind=quiz")
        return []
    variants: list[dict[str, Any]] = []
    for heading_number, title, body in _entries(text, label, "первого варианта '### id'", out):
        if VARIANT_ID.match(title) is None:
            out.add(label, f"строка {heading_number}: заголовок варианта — '### id'")
            continue
        variant: dict[str, Any] = {"id": title, "facts": [], "correct": [], "wrong": []}
        variants.append(variant)
        block: str | None = None
        for number, line in enumerate(lines(body), start=heading_number + 1):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped in (_FACTS_LABEL, _ANSWERS_LABEL):
                block = stripped
            elif block == _FACTS_LABEL and (fact := RUBRIC_ITEM.match(stripped)):
                variant["facts"].append({"id": fact.group(1), "text": fact.group(2).strip()})
            elif block == _ANSWERS_LABEL and (answer := ANSWER_ITEM.match(stripped)):
                key = "correct" if answer.group(1) == "+" else "wrong"
                variant[key].append(answer.group(2).strip())
            else:
                out.add(
                    label,
                    f"строка {number}: ожидается '### id', '{_FACTS_LABEL}', факт '- id: текст' "
                    f"или после '{_ANSWERS_LABEL}' ответ '+ верный' / '- неверный'",
                )
    _check_placeholders(label, text, params, out)
    for variant in variants:
        _check_variant(variant, label, out)
    if len(variants) < MIN_VARIANTS:
        out.add(
            label,
            f"нужно не меньше {MIN_VARIANTS} вариантов '### id': иначе утёкший ответ одного "
            f"экземпляра подходит ко всем; найдено {len(variants)}",
        )
    ids = [variant["id"] for variant in variants]
    for duplicate in sorted({i for i in ids if ids.count(i) > 1}):
        out.add(label, f"идентификатор варианта {duplicate!r} повторяется")
    answers = {tuple(variant["correct"]) for variant in variants}
    if len(variants) >= MIN_VARIANTS and len(answers) < 2:
        out.add(
            label,
            "у всех вариантов один и тот же верный ответ: утёкший ответ подходит к любому "
            "экземпляру; хотя бы два варианта должны вести к разным ответам",
        )
    return [
        {
            "id": variant["id"],
            "facts": variant["facts"],
            "correct": variant["correct"][0] if variant["correct"] else "",
            "wrong": variant["wrong"],
        }
        for variant in variants
    ]


def _check_variant(variant: dict[str, Any], label: str, out: _Collector) -> None:
    name = variant["id"]
    if not variant["facts"]:
        out.add(label, f"у варианта {name!r} нужен хотя бы один факт '- id: текст'")
    fact_ids = [fact["id"] for fact in variant["facts"]]
    for duplicate in sorted({i for i in fact_ids if fact_ids.count(i) > 1}):
        out.add(label, f"идентификатор факта {duplicate!r} в варианте {name!r} повторяется")
    if len(variant["correct"]) != 1:
        out.add(
            label,
            f"у варианта {name!r} должен быть ровно один верный ответ '+ текст', "
            f"найдено {len(variant['correct'])}",
        )
    if len(variant["wrong"]) < MIN_WRONG_ANSWERS:
        out.add(
            label,
            f"у варианта {name!r} нужно не меньше {MIN_WRONG_ANSWERS} неверных ответов "
            f"'- текст', найдено {len(variant['wrong'])}",
        )
    answers = [normalize_phrase(text) for text in variant["correct"] + variant["wrong"]]
    if len(set(answers)) != len(answers):
        out.add(label, f"ответы варианта {name!r} повторяются")


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


def _code_sections(
    sections: dict[str, str], meta: dict[str, Any], out: _Collector
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    runtime = meta.get("runtime")
    io = meta.get("io")

    entrypoint = None
    if io == IOKind.STDIO.value and SECTION_SIGNATURE in sections:
        out.add(
            section_label(SECTION_SIGNATURE),
            "у задания io=stdio нет функции: программа читает stdin и печатает ответ",
        )
    elif io != IOKind.STDIO.value:
        entrypoint = _entrypoint(sections, out)
    result["entrypoint"] = entrypoint
    result["check"] = _check(sections, meta, out)

    result["solutions"] = _solutions(sections, runtime, entrypoint, out)

    tests = _single_block(sections, SECTION_TESTS, out)
    if tests is not None:
        result["test_generator"] = tests.content
        label = section_label(SECTION_TESTS)
        if _block_language(tests) != "python":
            out.add(label, f"генератор тестов пишется на Python: ```python, а не ```{tests.info}")
        tree = _parse_python(tests.content, label, out)
        if tree is not None:
            generate = _top_functions(tree).get("generate")
            if generate is None or len(generate.args.args) < 2:
                out.add(label, "нужна функция верхнего уровня generate(params, rng)")
    return result


def _entrypoint(sections: dict[str, str], out: _Collector) -> str | None:
    signature = _single_block(sections, SECTION_SIGNATURE, out)
    if signature is None:
        return None
    line = signature.content.strip()
    if "\n" in line or not IDENTIFIER.match(line):
        out.add(
            section_label(SECTION_SIGNATURE),
            "блок должен содержать одну строку — имя функции из латиницы, цифр и _",
        )
        return None
    return line


def _check(sections: dict[str, str], meta: dict[str, Any], out: _Collector) -> Any:
    """Режим проверки ответа и свой проверяльщик из раздела «Проверяльщик».

    Режим без значения берётся по виду задания; неизвестный вид назовёт pydantic.
    """
    io = meta.get("io")
    mode = meta.get("check")
    if mode is None:
        if io not in set(IOKind):
            return None
        mode = DEFAULT_CHECK[IOKind(io)]
    elif mode not in get_args(CheckMode):
        out.add("check", f"неизвестный режим; допустимы: {', '.join(get_args(CheckMode))}")
        return None
    if io == IOKind.FUNCTION.value and mode not in FUNCTION_CHECKS:
        allowed = ", ".join(sorted(FUNCTION_CHECKS))
        out.add(
            "check",
            f"ответ функции сверяется как значение JSON; у io=function допустимы: {allowed}",
        )
    label = section_label(SECTION_CHECKER)
    checker = None
    if mode == "checker":
        block = _single_block(sections, SECTION_CHECKER, out, required_by="check: checker")
        name = block.info.split()[0] if block is not None and block.info else ""
        if block is not None and name not in language_runtimes():
            known = ", ".join(language_runtimes())
            out.add(
                label,
                f"метка блока кода — окружение песочницы, в котором запускается проверяльщик; "
                f"допустимы: {known}",
            )
        elif block is not None:
            checker = {"runtime": name, "code": block.content}
    elif SECTION_CHECKER in sections:
        out.add(label, "свой проверяльщик задаётся только вместе с check: checker")
    return {"mode": mode, "checker": checker}


SOLUTION_TITLE = re.compile(r"^([A-Za-z0-9_.-]+)\s+([+-])$")


def _solutions(
    sections: dict[str, str], runtime: Any, entrypoint: str | None, out: _Collector
) -> list[dict[str, Any]]:
    """Размеченные решения: '### id +' — верное, '### id -' — заведомо неверное.

    Под заголовком — необязательное описание текстом и ровно один блок кода. Метка блока —
    язык окружения runtime: окружение python-ml пишется на python.
    """
    language = source_language(runtime) if runtime in language_runtimes() else None
    text = sections.get(SECTION_SOLUTIONS)
    label = section_label(SECTION_SOLUTIONS)
    if text is None:
        out.add(label, "раздел обязателен для заданий kind=code")
        return []
    entries: list[tuple[str, bool, str]] = []
    for number, title, body in _entries(text, label, "первого решения '### id +'", out):
        parsed = SOLUTION_TITLE.match(title)
        if parsed is None:
            out.add(label, f"строка {number}: заголовок решения — '### id +' или '### id -'")
            entries.append(("", True, body))
        else:
            entries.append((parsed.group(1), parsed.group(2) == "+", body))

    ids = [solution_id for solution_id, _, _ in entries if solution_id]
    for duplicate in sorted({i for i in ids if ids.count(i) > 1}):
        out.add(label, f"идентификатор решения {duplicate!r} повторяется")
    if not any(correct for solution_id, correct, _ in entries if solution_id):
        out.add(label, "нужно хотя бы одно верное решение '### id +': по нему проверяются тесты")
    if not any(not correct for _, correct, _ in entries):
        out.add(
            label,
            "нужно хотя бы одно неверное решение '### id -': тесты должны его поймать, иначе "
            "никто не проверил, что они ловят ошибки",
        )

    solutions = []
    for solution_id, correct, body in entries:
        if not solution_id:
            continue
        blocks = _code_blocks(body)
        if len(blocks) != 1 or not blocks[0].content.strip():
            found = len(blocks) if len(blocks) != 1 else "пустой"
            out.add(
                label, f"у решения {solution_id!r} нужен ровно один непустой блок кода: {found}"
            )
            continue
        code = blocks[0].content
        where = f"{label}, решение {solution_id!r}"
        if language is not None and _block_language(blocks[0]) != language:
            out.add(
                where,
                f"у блока кода нужен язык окружения {runtime}: ```{language}, а не "
                f"```{blocks[0].info}",
            )
        if language == "python":
            tree = _parse_python(code, where, out)
            if tree is not None and entrypoint and entrypoint not in _top_functions(tree):
                out.add(where, f"нет функции верхнего уровня {entrypoint!r} из раздела Сигнатура")
        elif entrypoint and not re.search(rf"\b{re.escape(entrypoint)}\b", code):
            out.add(where, f"решение не упоминает функцию {entrypoint!r} из раздела Сигнатура")
        solutions.append(
            {
                "id": solution_id,
                "correct": correct,
                "code": code,
                "description": " ".join(_outside_blocks(body).split()),
            }
        )
    return solutions


def _entries(
    text: str, label: str, first_entry: str, out: _Collector
) -> list[tuple[int, str, str]]:
    """Записи раздела под заголовками ###: (номер строки заголовка, заголовок, тело).

    Номера строк считаются с 1 от начала раздела. Текст до первой записи — ошибка, и
    first_entry называет эту запись в сообщении.
    """
    rows = lines(text)
    starts = [h for h in headings(text) if h.level == ENTRY_LEVEL]
    first = starts[0].line if starts else len(rows)
    for number, row in enumerate(rows[:first], start=1):
        if row.strip():
            out.add(label, f"строка {number}: текст до {first_entry}")
    ends = [h.line for h in starts[1:]] + [len(rows)]
    return [
        (h.line + 1, h.title, "\n".join(rows[h.line + 1 : end]))
        for h, end in zip(starts, ends, strict=True)
    ]


def _outside_blocks(text: str) -> str:
    """Текст без блоков кода и их заборов."""
    inside = code_rows(text)
    return "\n".join(row for i, row in enumerate(lines(text)) if i not in inside)


def _single_block(
    sections: dict[str, str], name: str, out: _Collector, required_by: str = "kind=code"
) -> Fence | None:
    label = section_label(name)
    text = sections.get(name)
    if text is None:
        out.add(label, f"раздел обязателен для заданий {required_by}")
        return None
    blocks = _code_blocks(text)
    if len(blocks) != 1:
        out.add(label, f"нужен ровно один блок кода, найдено {len(blocks)}")
        return None
    if not blocks[0].content.strip():
        out.add(label, "блок кода пуст")
        return None
    return blocks[0]


def _block_language(block: Fence) -> str:
    """Язык из метки блока кода: синонимы вроде py и c++ сводятся к одному имени."""
    word = block.info.split()[0].lower() if block.info else ""
    return canonical_language(word) or word


def _code_blocks(text: str) -> list[Fence]:
    # Незакрытый блок уже отклонён разбором тела на разделы.
    return [fence for fence in fences(text) if fence.closed]


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
