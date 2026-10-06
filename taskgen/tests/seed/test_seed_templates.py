"""Шлюз банка шаблонов и демонстрационных вакансий из taskgen/seed.

Каждый шаблон разбирается, проходит справочники БД, а шаблон с кодом даёт на нескольких seed
кейсы, которые эталон проходит целиком в реальном Piston, а заведомо неверное решение — нет.
Вакансия ссылается только на коды из справочников, чтобы её можно было подать в отбор.
"""

from __future__ import annotations

import itertools
import json
import random
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import select

from taskgen.bank.importer import import_documents, read_directory
from taskgen.bank.parser import find_phrases, parse_template
from taskgen.config import SandboxSettings
from taskgen.contracts import (
    ParamSpec,
    RunLimits,
    TaskKind,
    TemplateSpec,
    TestCase,
    TestReport,
    TestRunRequest,
)
from taskgen.db import Area, AreaSkill, Grade
from taskgen.generation.harness import GeneratorError, generator_request, parse_generator_output
from taskgen.generation.params import draw_params, fill_placeholders
from taskgen.sandbox.piston import PistonRunner
from tests.seed.mutants import MUTANTS

SEED_DIR = Path(__file__).resolve().parents[2] / "seed" / "templates"
PATHS = sorted(SEED_DIR.rglob("*.md"))
VACANCIES = sorted((SEED_DIR.parent / "vacancies").glob("*.md"))
SEEDS = (0, 1, 2)
# Меньше кейсов не хватает на публичные примеры, граничные случаи и случайные входы.
MIN_CASES = 6
# Шаблоны одной области и грейда заявлены сопоставимыми по сложности.
MAX_CELL_SPREAD = 1
# Piston принимает тело запроса не больше 100 КиБ, а входы всех кейсов уходят одним
# запросом вместе с решением. Остаток оставлен под код кандидата.
MAX_INPUT_BYTES = 64 * 1024

SANDBOX = SandboxSettings()
LIMITS = RunLimits(timeout_ms=SANDBOX.run_timeout_ms, memory_bytes=SANDBOX.run_memory_limit_bytes)


def _source(path: Path) -> str:
    return path.relative_to(SEED_DIR).as_posix()


def _spec(path: Path) -> TemplateSpec:
    return parse_template(path.read_text(encoding="utf-8"), source=_source(path))


CODE_PATHS = [p for p in PATHS if _spec(p).kind is TaskKind.CODE]
ESSAY_PATHS = [p for p in PATHS if _spec(p).kind is TaskKind.ESSAY]


@pytest.fixture
async def piston():
    runner = PistonRunner(SANDBOX)
    yield runner
    await runner.aclose()


async def generate_cases(piston: PistonRunner, spec: TemplateSpec, seed: int) -> list[TestCase]:
    # Параметры и обвязка — те же функции, что у генерации экземпляра: шлюз проверяет
    # шаблон ровно так, как его исполнит сервис.
    params = draw_params(spec.params, random.Random(seed))
    result = await piston.run(generator_request(spec.test_generator or "", params, seed, LIMITS))
    try:
        cases = parse_generator_output(result)
    except GeneratorError as error:
        pytest.fail(f"{error}; параметры {params}")
    assert len(cases) >= MIN_CASES, (params, cases)
    # Объект именованных аргументов сервис принимает, но обвязка C++ понимает только массив.
    assert all(isinstance(case.input, list) for case in cases), cases
    distinct = {json.dumps(case.expected, sort_keys=True) for case in cases}
    assert len(distinct) > 1, f"все кейсы ждут один ответ {distinct}"
    size = len(json.dumps([case.input for case in cases], ensure_ascii=False).encode())
    assert size <= MAX_INPUT_BYTES, f"входы кейсов занимают {size} байт"
    return cases


async def run_solution(
    piston: PistonRunner, spec: TemplateSpec, solution: str, cases: list[TestCase]
) -> TestReport:
    return await piston.run_tests(
        TestRunRequest(
            runtime=spec.runtime or "",
            entrypoint=spec.entrypoint or "",
            solution=solution,
            cases=cases,
            limits=LIMITS,
        )
    )


def wrong_solutions(spec: TemplateSpec, cases: list[TestCase]) -> dict[str, str]:
    """Неверные решения, которые тесты шаблона обязаны отличить от эталона: мутанты эталона
    из mutants.py и решения, которые не смотрят на вход."""
    reference = spec.reference_solution or ""
    solutions = {}
    for description, old, new in MUTANTS[spec.name]:
        assert old in reference, f"мутант «{description}» не находит фрагмент {old!r}"
        solutions[description] = reference.replace(old, new)
    return solutions | constant_solutions(spec, cases)


def constant_solutions(spec: TemplateSpec, cases: list[TestCase]) -> dict[str, str]:
    entry = spec.entrypoint or ""
    if spec.runtime == "cpp":
        signature = re.search(
            rf"^([^\n;{{}}()]+?)\b{entry}\s*\(([^)]*)\)\s*\{{",
            spec.reference_solution or "",
            re.MULTILINE,
        )
        assert signature, f"в эталоне не найдено определение {entry}"
        result, args = signature.group(1).strip(), signature.group(2)
        return {"default": f"{result} {entry}({args}) {{ return {{}}; }}\n"}
    first = cases[0].expected
    default = type(first)() if first is not None else 0
    return {
        name: f"import json\n\n\ndef {entry}(*args, **kwargs):\n"
        f"    return json.loads({json.dumps(json.dumps(value))})\n"
        for name, value in (("default", default), ("first-example", first))
    }


def param_variants(specs: dict[str, ParamSpec]) -> list[dict[str, Any]]:
    """Все сочетания значений choice и границы числовых параметров: у эссе их единицы."""
    names = sorted(specs)
    values = [
        specs[name].choices if specs[name].type == "choice" else [specs[name].min, specs[name].max]
        for name in names
    ]
    return [dict(zip(names, combo, strict=True)) for combo in itertools.product(*values)]


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


@pytest.mark.integration
async def test_bank_covers_every_cell_and_imports(db_session):
    areas = set(await db_session.scalars(select(Area.code)))
    grades = set(await db_session.scalars(select(Grade.code)))
    kinds: dict[tuple[str, str], list[TaskKind]] = defaultdict(list)
    for path in PATHS:
        spec = _spec(path)
        kinds[spec.area, spec.grade].append(spec.kind)

    gaps = {
        (area, grade): kinds[area, grade]
        for area in areas
        for grade in grades
        if len(kinds[area, grade]) < 3 or set(kinds[area, grade]) != set(TaskKind)
    }
    assert not gaps, gaps
    # Импорт проверяет навыки по справочнику и их связь с областью; транзакция откатывается.
    outcomes = await import_documents(db_session, read_directory(SEED_DIR))
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


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("path", CODE_PATHS, ids=_source)
async def test_reference_passes_generated_cases(piston, path: Path, seed: int):
    spec = _spec(path)
    cases = await generate_cases(piston, spec, seed)

    report = await run_solution(piston, spec, spec.reference_solution or "", cases)

    assert report.compile_error is None, report.compile_error
    failed = [(i, o.error) for i, o in enumerate(report.outcomes) if not o.passed]
    assert not failed, failed


def test_every_code_template_has_mutants():
    names = {_spec(path).name for path in CODE_PATHS}

    assert names == set(MUTANTS), names ^ set(MUTANTS)


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("path", CODE_PATHS, ids=_source)
async def test_wrong_solutions_fail_some_case(piston, path: Path, seed: int):
    spec = _spec(path)
    cases = await generate_cases(piston, spec, seed)

    survivors = []
    for name, solution in wrong_solutions(spec, cases).items():
        report = await run_solution(piston, spec, solution, cases)
        assert report.compile_error is None, (name, report.compile_error)
        if report.pass_rate == 1.0:
            survivors.append(name)
    assert not survivors, f"неверные решения прошли все кейсы: {survivors}"
