"""Проверка шаблона в песочнице: общий код импорта и шлюза банка.

Песочница здесь — LocalSandbox: проверка исполняет настоящий генератор и настоящие решения
шаблона, а Piston нужен только интеграционным тестам импорта.
"""

from __future__ import annotations

import pytest

from taskgen.bank.parser import parse_template
from taskgen.bank.validation import MIN_CASES, TemplateValidator, static_problems
from taskgen.config import ImportCheckSettings, ScoringSettings
from taskgen.contracts import RunLimits, TemplateSpec
from taskgen.sandbox.errors import SandboxServiceError
from tests.bank.factory import (
    REFERENCE,
    SHORT_RIGHT,
    STDIO_REFERENCE,
    STDIO_SKIPS_EMPTY,
    make_essay,
    make_stdio,
    make_template,
    solution,
)
from tests.generation.fakes import LocalSandbox

LIMITS = RunLimits(timeout_ms=5000, memory_bytes=256 * 1024 * 1024)
# Перестановка индексов пары не находит пару [1, 2] в первом кейсе, но проходит кейсы без пары.
SWAPPED = REFERENCE.replace("return [i, j]", "return [j, i]")


def spec_of(text: str, source: str = "t.md") -> TemplateSpec:
    return parse_template(text, source=source)


def validator(sandbox=None, seeds: int = 2, concurrency: int = 2) -> TemplateValidator:
    return TemplateValidator(
        LocalSandbox() if sandbox is None else sandbox,
        check=ImportCheckSettings(seeds=seeds, concurrency=concurrency),
        limits=LIMITS,
        scoring=ScoringSettings(),
    )


def with_solutions(*parts: str, **frontmatter) -> str:
    return make_template(sections={"Решения": "\n\n".join(parts)}, **frontmatter)


async def issues_of(text: str, **options) -> list:
    return await validator(**options).check([("t.md", spec_of(text))])


async def test_valid_template_passes_on_every_seed():
    sandbox = LocalSandbox()
    assert await validator(sandbox, seeds=3).check([("t.md", spec_of(make_template()))]) == []
    # Генератор запущен на каждом seed, а каждое решение — на кейсах каждого seed.
    seeds = sorted({run.stdin.split('"seed": ')[1].rstrip("}") for run in sandbox.runs})
    assert seeds == ["0", "1", "2"]
    solutions = {run.solution for run in sandbox.test_runs}
    assert {REFERENCE.rstrip("\n"), SHORT_RIGHT.rstrip("\n")} <= solutions


async def test_every_correct_solution_is_run_and_a_failing_one_is_named_with_seed_and_case():
    text = with_solutions(
        solution("reference", "+", REFERENCE),
        solution("swapped", "+", SWAPPED),
        solution("short-right", "-", SHORT_RIGHT),
    )
    issues = await issues_of(text)

    assert {issue.location for issue in issues} == {"seed 0", "seed 1"}
    for issue in issues:
        assert issue.source == "t.md"
        assert "верное решение 'swapped'" in issue.message
        assert "кейсы [1, 2, 3, 6] из 6" in issue.message


async def test_wrong_solution_that_passes_every_case_is_named():
    # Неверное решение, которое на деле совпадает с верным: тесты его не ловят.
    text = with_solutions(
        solution("reference", "+", REFERENCE),
        solution("same", "-", REFERENCE, "Ничем не отличается"),
    )
    issues = await issues_of(text)

    assert [issue.location for issue in issues] == ["seed 0", "seed 1"]
    assert all("неверное решение 'same'" in i.message for i in issues)
    assert all("Ничем не отличается" in i.message for i in issues)


async def test_wrong_solution_failing_only_a_case_outside_required_groups_survives():
    # Решение проваливает один кейс из необязательной группы: балл выше порога, гейта нет.
    # Это строже, чем «провалить хотя бы один кейс», и совпадает с прежним шлюзом банка.
    groups = {"main": {"weight": 1, "required": True}, "extra": {"weight": 0.01}}
    generator = (
        "```python\n"
        "def generate(params, rng):\n"
        "    main = [\n"
        "        {'input': [[1, 2, 3], 5], 'expected': [1, 2]},\n"
        "        {'input': [[2, 7], 9], 'expected': [0, 1]},\n"
        "        {'input': [[1, 2], 10], 'expected': []},\n"
        "        {'input': [[], 0], 'expected': []},\n"
        "        {'input': [[-3, 0, 4, 8], 5], 'expected': [0, 3]},\n"
        "    ]\n"
        "    extra = [{'input': [[1, 2, 3, 9], 12], 'expected': [2, 3]}]\n"
        "    main = [dict(c, group='main') for c in main]\n"
        "    return main + [dict(c, group='extra') for c in extra]\n"
        "```"
    )
    # Пара с суммой 12 бывает только в кейсе extra.
    blind = REFERENCE.replace("if s == target:", "if s == target and s != 12:")
    text = make_template(
        case_groups=groups,
        sections={
            "Тесты": generator,
            "Решения": solution("reference", "+", REFERENCE)
            + "\n\n"
            + solution("blind", "-", blind),
        },
    )
    issues = await issues_of(text, seeds=1)
    assert [i.location for i in issues] == ["seed 0"]
    assert "'blind'" in issues[0].message and "решённым" in issues[0].message


async def test_wrong_solution_that_does_not_compile_is_reported():
    # Неверное решение с ошибкой компиляции проваливает всё и потому ничего не проверяет.
    sandbox = LocalSandbox()
    wrong = SHORT_RIGHT.rstrip("\n")

    def tamper(request, report):
        if request.solution == wrong:
            return report.model_copy(update={"compile_error": "expected ';'"})
        return report

    sandbox.tamper = tamper
    issues = await issues_of(make_template(), sandbox=sandbox, seeds=1)
    [issue] = issues
    assert "'short-right'" in issue.message and "expected ';'" in issue.message


async def test_constant_answers_must_be_caught():
    generator = (
        "```python\n"
        "def generate(params, rng):\n"
        "    return [{'input': [[1, 2], 10], 'expected': [], 'group': 'main'}] * 6\n"
        "```"
    )
    issues = await issues_of(make_template(sections={"Тесты": generator}), seeds=1)
    messages = " ".join(i.message for i in issues)
    assert "все кейсы ждут один ответ" in messages
    assert "решение-константа [] засчитано решённым" in messages


async def test_few_cases_and_object_inputs_are_reported():
    generator = (
        "```python\n"
        "def generate(params, rng):\n"
        "    return [\n"
        "        {'input': {'nums': [1, 2, 3], 'target': 5},\n"
        "         'expected': [1, 2], 'group': 'main'},\n"
        "        {'input': [[1, 2], 10], 'expected': [], 'group': 'main'},\n"
        "    ]\n"
        "```"
    )
    issues = await issues_of(make_template(sections={"Тесты": generator}), seeds=1)
    messages = " ".join(i.message for i in issues)
    assert f"нужно не меньше {MIN_CASES}" in messages
    assert "массив" in messages


async def test_generator_failure_is_reported_with_params():
    generator = "```python\ndef generate(params, rng):\n    raise RuntimeError('нет данных')\n```"
    issues = await issues_of(make_template(sections={"Тесты": generator}), seeds=1)
    [issue] = issues
    assert issue.location == "seed 0" and "нет данных" in issue.message and "'n':" in issue.message


def test_code_template_needs_a_required_case_group():
    assert static_problems(spec_of(make_template())) == []
    found = static_problems(spec_of(make_template(case_groups=None)))
    assert [i.location for i in found] == ["case_groups"]
    optional = {"main": {"weight": 1, "required": False}}
    found = static_problems(spec_of(make_template(case_groups=optional)))
    assert [i.location for i in found] == ["case_groups"]


async def test_one_broken_template_does_not_hide_the_others():
    good = spec_of(make_template(name="good"), "good.md")
    generator = "```python\ndef generate(params, rng):\n    raise RuntimeError('сломан')\n```"
    bad = spec_of(make_template(name="bad", sections={"Тесты": generator}), "bad.md")
    swapped = spec_of(
        with_solutions(
            solution("reference", "+", SWAPPED), solution("short-right", "-", SHORT_RIGHT)
        ),
        "swapped.md",
    )
    issues = await validator(seeds=1).check([("bad.md", bad), ("good.md", good), ("s.md", swapped)])
    assert {issue.source for issue in issues} == {"bad.md", "s.md"}


async def test_templates_without_code_need_no_sandbox():
    essay = spec_of(make_essay(), "e.md")
    checker = TemplateValidator(
        None, check=ImportCheckSettings(), limits=LIMITS, scoring=ScoringSettings()
    )
    assert await checker.check([("e.md", essay)]) == []


async def test_missing_or_down_sandbox_is_an_explicit_error():
    code = spec_of(make_template())
    checker = TemplateValidator(
        None, check=ImportCheckSettings(), limits=LIMITS, scoring=ScoringSettings()
    )
    with pytest.raises(SandboxServiceError, match="не настроена"):
        await checker.check([("t.md", code)])

    down = LocalSandbox()
    down.down = True
    with pytest.raises(SandboxServiceError, match="недоступна"):
        await validator(down).check([("t.md", code)])


async def test_unknown_runtime_is_a_template_issue_not_a_sandbox_failure():
    text = make_template(runtime="brainfuck")
    issues = await TemplateValidator(
        _UnknownRuntimeSandbox(),
        check=ImportCheckSettings(seeds=1),
        limits=LIMITS,
        scoring=ScoringSettings(),
    ).check([("t.md", spec_of(text))])
    assert issues and "brainfuck" in issues[0].message


# --- Задачи stdio ----------------------------------------------------------------------------


async def test_valid_stdio_template_passes_and_runs_programs_per_case():
    sandbox = LocalSandbox()
    spec = spec_of(make_stdio())
    assert await validator(sandbox, seeds=1).check([("s.md", spec)]) == []
    programs = {run.files.get("solution.py") for run in sandbox.runs}
    assert {STDIO_REFERENCE.rstrip("\n"), STDIO_SKIPS_EMPTY.rstrip("\n")} <= programs


async def test_stdio_wrong_solution_that_passes_is_named():
    text = make_stdio(
        sections={
            "Решения": solution("reference", "+", STDIO_REFERENCE)
            + "\n\n"
            + solution("same", "-", STDIO_REFERENCE, "Совпадает с верным")
        }
    )
    issues = await issues_of(text, seeds=1)
    assert any("неверное решение 'same'" in i.message for i in issues)


async def test_stdio_constant_output_must_be_caught():
    # Все кейсы ждут один вывод: программа, которая печатает его не читая вход, проходит.
    generator = (
        "```python\n"
        "def generate(params, rng):\n"
        "    return [{'stdin': f'{i} {-i}\\n', 'expected': '0\\n', 'group': 'main'}\n"
        "            for i in range(6)]\n"
        "```"
    )
    issues = await issues_of(make_stdio(sections={"Тесты": generator}), seeds=1)
    messages = " ".join(i.message for i in issues)
    assert "все кейсы ждут один ответ" in messages
    assert "решение-константа" in messages and "засчитано решённым" in messages


async def test_stdio_expected_equal_under_tokens_counts_as_one_answer():
    # "0" и "0\n" для режима tokens один ответ: константа угадала бы оба.
    generator = (
        "```python\n"
        "def generate(params, rng):\n"
        "    return [{'stdin': f'{i} {-i}\\n', 'expected': '0' + '\\n' * (i % 2), "
        "'group': 'main'} for i in range(6)]\n"
        "```"
    )
    issues = await issues_of(make_stdio(sections={"Тесты": generator}), seeds=1)
    assert any("все кейсы ждут один ответ" in i.message for i in issues)


async def test_stdio_case_too_large_for_one_run_is_reported():
    generator = (
        "```python\n"
        "def generate(params, rng):\n"
        "    big = '1 ' * 40000 + '\\n'\n"
        "    return [{'stdin': big if i == 0 else f'{i}\\n', 'expected': f'{i}\\n', "
        "'group': 'main'} for i in range(6)]\n"
        "```"
    )
    issues = await issues_of(make_stdio(sections={"Тесты": generator}), seeds=1)
    assert any("кейс 1" in i.message and "байт" in i.message for i in issues)


async def test_stdio_checker_failing_on_correct_solution_is_reported():
    broken = "```python\nimport sys\nsys.exit(2)\n```"
    text = make_stdio(check="checker", sections={"Проверяльщик": broken})
    issues = await issues_of(text, seeds=1)
    assert any("верное решение 'reference'" in i.message for i in issues)
    assert any("проверяльщик отказал" in i.message for i in issues)


async def test_checker_failure_on_wrong_solution_is_reported_not_counted_as_catch():
    # Проверяльщик, который вместо «неверно» падает: неверное решение «поймано» его отказом.
    crashing = (
        "```python\nimport sys\n_, out, ans = (open(n).read().split() for n in sys.argv[1:4])\n"
        "sys.exit(0 if out == ans else 2)\n```"
    )
    text = make_stdio(check="checker", sections={"Проверяльщик": crashing})
    issues = await issues_of(text, seeds=1)
    assert any("'skips-empty'" in i.message and "проверяльщик отказал" in i.message for i in issues)


class _UnknownRuntimeSandbox(LocalSandbox):
    async def run_tests(self, request):
        from taskgen.sandbox.registry import RuntimeRegistry

        RuntimeRegistry.load().get(request.runtime)
        return await super().run_tests(request)
