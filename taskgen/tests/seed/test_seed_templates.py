"""Шлюз банка шаблонов и демонстрационных вакансий из taskgen/seed.

Каждый шаблон разбирается, проходит справочники БД, а шаблон с кодом даёт на нескольких seed
кейсы, которые эталон проходит целиком в реальном Piston, а заведомо неверное решение — нет.
Вакансия ссылается только на коды из справочников, чтобы её можно было подать в отбор.
"""

from __future__ import annotations

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
from taskgen.bank.parser import parse_template
from taskgen.config import SandboxSettings
from taskgen.contracts import (
    ParamSpec,
    RunLimits,
    RunRequest,
    TaskKind,
    TemplateSpec,
    TestCase,
    TestReport,
    TestRunRequest,
)
from taskgen.db import Area, AreaSkill, Grade
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

GENERATOR_HARNESS = """\
import json
import random
import sys

payload = json.loads(sys.stdin.read())
namespace = {"__name__": "taskgen_generator"}
exec(compile(payload["source"], "generator.py", "exec"), namespace)
cases = namespace["generate"](payload["params"], random.Random(payload["seed"]))
sys.stdout.write(json.dumps(cases, ensure_ascii=False, allow_nan=False))
"""


def _source(path: Path) -> str:
    return path.relative_to(SEED_DIR).as_posix()


def _spec(path: Path) -> TemplateSpec:
    return parse_template(path.read_text(encoding="utf-8"), source=_source(path))


CODE_PATHS = [p for p in PATHS if _spec(p).kind is TaskKind.CODE]


def draw_params(specs: dict[str, ParamSpec], rng: random.Random) -> dict[str, Any]:
    """Значения по тем же правилам, что у генерации экземпляра: имена по алфавиту, int из
    отрезка [min, max], float равномерно, choice — один из вариантов."""
    values: dict[str, Any] = {}
    for name in sorted(specs):
        spec = specs[name]
        if spec.type == "int":
            values[name] = rng.randint(int(spec.min), int(spec.max))
        elif spec.type == "float":
            values[name] = rng.uniform(spec.min, spec.max)
        else:
            values[name] = rng.choice(spec.choices)
    return values


@pytest.fixture
async def piston():
    runner = PistonRunner(SANDBOX)
    yield runner
    await runner.aclose()


async def generate_cases(piston: PistonRunner, spec: TemplateSpec, seed: int) -> list[TestCase]:
    params = draw_params(spec.params, random.Random(seed))
    payload = {"source": spec.test_generator, "params": params, "seed": seed}
    result = await piston.run(
        RunRequest(
            runtime="python",
            files={"main.py": GENERATOR_HARNESS},
            stdin=json.dumps(payload, ensure_ascii=False),
            limits=LIMITS,
        )
    )
    assert result.exit_code == 0 and not result.timed_out, (params, result.stderr[-2000:])
    raw = json.loads(result.stdout)
    assert isinstance(raw, list) and len(raw) >= MIN_CASES, (params, raw)
    for item in raw:
        assert isinstance(item, dict) and isinstance(item.get("input"), list), item
        assert "expected" in item, item
    distinct = {json.dumps(item["expected"], sort_keys=True) for item in raw}
    assert len(distinct) > 1, f"все кейсы ждут один ответ {distinct}"
    size = len(json.dumps([item["input"] for item in raw], ensure_ascii=False).encode())
    assert size <= MAX_INPUT_BYTES, f"входы кейсов занимают {size} байт"
    return [TestCase.model_validate(item) for item in raw]


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
