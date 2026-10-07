"""Шлюз банка шаблонов и демонстрационных вакансий из taskgen/seed.

Весь банк импортируется в БД тем же кодом, что и `taskgen import`: разбор, прогон шаблонов с
кодом в реальном Piston (bank/validation.py) и справочники. Сверх импорта шлюз проверяет
правила, которые касаются банка целиком: покрытие ячеек, сопоставимость сложности, задачки
и вопросы эссе при каждом сочетании параметров. Вакансия ссылается только на коды из
справочников, чтобы её можно было подать в отбор.
"""

from __future__ import annotations

import itertools
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import select

from taskgen.bank.importer import import_documents, read_directory
from taskgen.bank.parser import TemplateError, find_phrases, parse_template
from taskgen.bank.validation import TemplateValidator, static_problems
from taskgen.config import SelectionSettings, Settings
from taskgen.contracts import ParamSpec, TaskKind, TemplateSpec
from taskgen.db import Area, AreaSkill, Grade
from taskgen.generation.params import fill_placeholders
from taskgen.sandbox.piston import PistonRunner
from tests.generation.world import TEST_AREA_TITLE

SEED_DIR = Path(__file__).resolve().parents[2] / "seed" / "templates"
PATHS = sorted(SEED_DIR.rglob("*.md"))
VACANCIES = sorted((SEED_DIR.parent / "vacancies").glob("*.md"))
# Шаблоны одной области и грейда заявлены сопоставимыми по сложности.
MAX_CELL_SPREAD = 1


def _source(path: Path) -> str:
    return path.relative_to(SEED_DIR).as_posix()


def _spec(path: Path) -> TemplateSpec:
    return parse_template(path.read_text(encoding="utf-8"), source=_source(path))


CODE_PATHS = [p for p in PATHS if _spec(p).kind is TaskKind.CODE]
ESSAY_PATHS = [p for p in PATHS if _spec(p).kind is TaskKind.ESSAY]
QUIZ_PATHS = [p for p in PATHS if _spec(p).kind is TaskKind.QUIZ]
# Задачек в ячейке не меньше двух: одна задачка ячейки выпадала бы каждому кандидату.
MIN_QUIZ_PER_CELL = 2


@pytest.fixture
async def validator():
    """Проверка импорта в реальном Piston с настройками сервиса по умолчанию."""
    settings = Settings()
    runner = PistonRunner(settings.sandbox)
    yield TemplateValidator.from_settings(runner, settings)
    await runner.aclose()


def param_variants(specs: dict[str, ParamSpec]) -> list[dict[str, Any]]:
    """Все сочетания значений choice и границы числовых параметров: у эссе их единицы."""
    names = sorted(specs)
    values = [
        specs[name].choices if specs[name].type == "choice" else [specs[name].min, specs[name].max]
        for name in names
    ]
    return [dict(zip(names, combo, strict=True)) for combo in itertools.product(*values)]


def quiz_problems(spec: TemplateSpec) -> list[str]:
    """Нарушения правил банка для задачки сверх тех, что проверяет разбор.

    Разбор сверяет сырой текст, а фраза может сложиться только после подстановки
    параметров, поэтому здесь сверяется готовый текст при каждом сочетании параметров.
    """
    problems = []
    if not spec.forbidden:
        problems.append("нет раздела «Нельзя раскрывать»: кейс может назвать ответ")
    for variant in spec.variants:
        if len(variant.wrong) < 3:
            problems.append(f"{variant.id}: неверных ответов {len(variant.wrong)}, нужно 3")
        # Верный ответ, которого нет среди запрещённых фраз, кейс может назвать прямо.
        if not find_phrases(variant.correct, spec.forbidden):
            problems.append(f"{variant.id}: верный ответ не закрыт ни одной фразой запрета")
    if len({variant.correct for variant in spec.variants}) < 2:
        problems.append("у всех вариантов один верный ответ")
    # Один набор ответов у всех вариантов: по набору ответов вариант не узнать.
    answer_sets = {frozenset([v.correct, *v.wrong]) for v in spec.variants}
    if len(answer_sets) != 1:
        problems.append("у вариантов разные наборы ответов: по ним узнаётся вариант")
    for params in param_variants(spec.params):
        texts = {"вопрос": fill_placeholders(spec.question or "", params)}
        for variant in spec.variants:
            for fact in variant.facts:
                texts[f"факт {variant.id}.{fact.id}"] = fill_placeholders(fact.text, params)
        for where, text in texts.items():
            for phrase in find_phrases(text, spec.forbidden):
                problems.append(f"{where} содержит запрещённую фразу {phrase!r} при {params}")
    return problems


@pytest.mark.parametrize("path", QUIZ_PATHS, ids=_source)
def test_quiz_variants_are_unambiguous_and_hide_answers(path: Path):
    spec = _spec(path)
    assert spec.question and len(spec.variants) >= 2
    assert not quiz_problems(spec), quiz_problems(spec)


def test_quiz_check_reports_broken_template():
    # Проверка правил банка сама должна краснеть: ответ не закрыт запретом, наборы ответов
    # разные, а вопрос после подстановки раскрывает ответ.
    spec = _spec(QUIZ_PATHS[0])
    first, second, *_ = spec.variants
    broken = spec.model_copy(
        update={
            "question": "Подойдёт ли {domain}?",
            "forbidden": [
                phrase for phrase in spec.forbidden if not find_phrases(first.correct, [phrase])
            ]
            + [spec.params["domain"].choices[0]],
            "variants": [first, second.model_copy(update={"wrong": second.wrong[:2] + ["x"]})],
        }
    )
    problems = quiz_problems(broken)
    assert any("не закрыт" in problem for problem in problems), problems
    assert any("наборы ответов" in problem for problem in problems), problems
    assert any("вопрос содержит" in problem for problem in problems), problems


def test_every_cell_has_enough_quiz_templates():
    cells: Counter[tuple[str, str]] = Counter(
        (spec.area, spec.grade) for spec in map(_spec, QUIZ_PATHS)
    )
    every = {(spec.area, spec.grade) for spec in map(_spec, PATHS)}
    short = {cell: cells[cell] for cell in every if cells[cell] < MIN_QUIZ_PER_CELL}
    assert not short, f"задачек меньше {MIN_QUIZ_PER_CELL} в ячейках: {short}"


@pytest.mark.parametrize("path", ESSAY_PATHS, ids=_source)
def test_essay_questions_do_not_reveal_forbidden_phrases(path: Path):
    spec = _spec(path)

    # В банке «Нельзя раскрывать» обязателен: без него проверка ситуации ловит только
    # пропущенные факты, а подсказки в ситуации проходят.
    assert spec.questions and spec.scenario_facts and spec.forbidden
    # Разбор сверяет сырой текст вопросов и значения параметров по отдельности, а фраза
    # может сложиться только после подстановки, поэтому шлюз сверяет готовый текст.
    revealed = {
        phrase
        for params in param_variants(spec.params)
        for phrase in find_phrases(fill_placeholders(spec.questions, params), spec.forbidden)
    }
    assert not revealed, f"вопросы сами раскрывают ответ: {sorted(revealed)}"


@pytest.mark.parametrize("path", PATHS, ids=_source)
def test_template_parses_and_lives_in_its_cell(path: Path):
    spec = _spec(path)

    assert path.relative_to(SEED_DIR).parts == (spec.area, spec.grade, f"{spec.name}.md")


def test_cells_are_comparable_in_difficulty():
    cells: dict[tuple[str, str], list[int]] = defaultdict(list)
    for path in PATHS:
        spec = _spec(path)
        cells[spec.area, spec.grade].append(spec.difficulty)

    spread = {cell: max(d) - min(d) for cell, d in cells.items()}
    assert spread and all(s <= MAX_CELL_SPREAD for s in spread.values()), spread


def coverage_gaps(
    specs: list[TemplateSpec], areas: set[str], grades: set[str], selection: SelectionSettings
) -> list[str]:
    """Чего не хватает банку, чтобы собрать тест с составом selection на любую ячейку.

    Квотные слоты типа берут шаблоны только заявленного грейда, поэтому в каждой ячейке их
    нужно не меньше квоты. Отбор идёт без возвращения, поэтому в области шаблонов типа нужно
    не меньше, чем заданий этого типа в тесте: иначе тест тихо выйдет короче.
    """
    counts: Counter[tuple[str, str, TaskKind]] = Counter(
        (spec.area, spec.grade, spec.kind) for spec in specs
    )
    gaps = []
    for kind in TaskKind:
        tasks = selection.tasks_of(kind)
        if tasks == 0:
            continue
        quota = min(selection.min_claimed_per_kind, tasks)
        for area in sorted(areas):
            in_area = sum(counts[area, grade, kind] for grade in grades)
            if in_area < tasks:
                gaps.append(
                    f"{area}: шаблонов {kind.value} {in_area}, а тест берёт {tasks}; "
                    "добавьте шаблоны или уменьшите число заданий типа"
                )
            for grade in sorted(grades):
                if counts[area, grade, kind] < quota:
                    gaps.append(
                        f"{area}/{grade}: шаблонов {kind.value} {counts[area, grade, kind]}, "
                        f"а квота min_claimed_per_kind требует {quota}"
                    )
    return gaps


def test_coverage_check_names_what_is_missing():
    # Проверка покрытия сама должна краснеть: банк без эссе middle и с одним кодом на область.
    specs = [
        _spec(PATHS[0]).model_copy(update={"area": "a", "grade": g, "kind": TaskKind.CODE})
        for g in ("junior", "middle")
    ] + [
        _spec(PATHS[0]).model_copy(update={"area": "a", "grade": "junior", "kind": TaskKind.ESSAY})
    ]
    selection = SelectionSettings(code_tasks=3, essay_tasks=1, quiz_tasks=0)
    gaps = coverage_gaps(specs, {"a"}, {"junior", "middle"}, selection)
    assert gaps == [
        "a: шаблонов code 2, а тест берёт 3; добавьте шаблоны или уменьшите число заданий типа",
        "a/middle: шаблонов essay 0, а квота min_claimed_per_kind требует 1",
    ]


@pytest.mark.integration
async def test_bank_covers_every_cell(db_session):
    areas = set(await db_session.scalars(select(Area.code).where(Area.title != TEST_AREA_TITLE)))
    grades = set(await db_session.scalars(select(Grade.code)))
    gaps = coverage_gaps([_spec(path) for path in PATHS], areas, grades, SelectionSettings())
    assert not gaps, "\n".join(gaps)


@pytest.mark.integration
async def test_bank_imports_with_sandbox_check(db_session, validator):
    # Тот же импорт, что у taskgen import: каждое верное решение проходит все кейсы на каждом
    # проверяемом seed, каждое неверное и решения-константы не решены, навыки есть в
    # справочнике и относятся к области шаблона. Транзакция теста откатывается.
    try:
        outcomes = await import_documents(db_session, read_directory(SEED_DIR), check=validator)
    except TemplateError as error:
        pytest.fail("\n".join(str(issue) for issue in error.issues))
    assert len(outcomes) == len(PATHS)


@pytest.mark.integration
@pytest.mark.parametrize("path", VACANCIES, ids=lambda p: p.name)
async def test_vacancy_names_area_grade_and_skills_of_that_area(db_session, path: Path):
    meta = yaml.safe_load(path.read_text(encoding="utf-8").split("---")[1])
    areas = set(await db_session.scalars(select(Area.code)))
    grades = set(await db_session.scalars(select(Grade.code)))
    rows = await db_session.execute(select(AreaSkill.area_code, AreaSkill.skill_code))
    area_skills = {(area, skill) for area, skill in rows}

    assert meta["area"] in areas and meta["grade"] in grades, meta
    foreign = [s for s in meta["skills"] if (meta["area"], s) not in area_skills]
    assert meta["skills"] and not foreign, foreign


@pytest.mark.parametrize("path", CODE_PATHS, ids=_source)
def test_code_template_follows_rules_that_need_no_sandbox(path: Path):
    # Те же правила проверяет импорт; здесь они видны без Postgres и Piston.
    assert static_problems(_spec(path)) == []
