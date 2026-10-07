"""Обвязка генератора тестов: код шаблона исполняется в песочнице, а не в сервисе."""

import json
import random

import pytest

from taskgen.contracts import CaseGroup, IOKind, RunLimits, RunResult
from taskgen.generation.harness import GeneratorError, generator_request
from taskgen.generation.harness import parse_generator_output as parse_any
from tests.generation.fakes import LocalSandbox

LIMITS = RunLimits(timeout_ms=5000, memory_bytes=256 * 1024 * 1024)
FUNCTION, STDIO = IOKind.FUNCTION, IOKind.STDIO


def parse_generator_output(result, groups=None, *, io=FUNCTION):
    return parse_any(result, groups, io=io)


GENERATOR = """
def generate(params, rng):
    n = params["n"]
    return [{"input": [rng.randint(0, 1000) for _ in range(n)], "expected": n}
            for _ in range(3)]
"""


async def _run(source: str, params=None, seed: int = 5):
    request = generator_request(source, params or {"n": 4}, seed, LIMITS)
    return request, await LocalSandbox().run(request)


async def test_harness_runs_generate_with_seeded_rng():
    request, result = await _run(GENERATOR)
    assert request.runtime == "python"
    cases = parse_generator_output(result)
    assert len(cases) == 3
    assert all(case.expected == 4 and len(case.input) == 4 for case in cases)
    # rng внутри песочницы — random.Random(seed): тот же seed даёт те же кейсы, что и здесь.
    rng = random.Random(5)
    expected_first = [rng.randint(0, 1000) for _ in range(4)]
    assert cases[0].input == expected_first


async def test_same_seed_same_cases_other_seed_other_cases():
    _, first = await _run(GENERATOR, seed=11)
    _, again = await _run(GENERATOR, seed=11)
    _, other = await _run(GENERATOR, seed=12)
    assert parse_generator_output(first) == parse_generator_output(again)
    assert parse_generator_output(first) != parse_generator_output(other)


async def test_print_inside_generator_does_not_break_output():
    source = (
        "def generate(params, rng):\n"
        "    print('отладка')\n"
        "    return [{'input': [1], 'expected': 2}]\n"
    )
    _, result = await _run(source)
    assert [case.expected for case in parse_generator_output(result)] == [2]


async def test_generator_names_do_not_clash_with_harness():
    source = (
        "import json\njson = None\nsys = None\n"
        "def generate(params, rng):\n    return [{'input': [1], 'expected': 1}]\n"
    )
    _, result = await _run(source)
    assert len(parse_generator_output(result)) == 1


async def test_tuples_become_lists():
    source = "def generate(params, rng):\n    return [{'input': (1, 2), 'expected': (3,)}]\n"
    _, result = await _run(source)
    [case] = parse_generator_output(result)
    assert case.input == [1, 2] and case.expected == [3]


@pytest.mark.parametrize(
    ("source", "fragment"),
    [
        ("def generate(params, rng):\n    raise ValueError('сломан')\n", "сломан"),
        ("def generate(params, rng):\n    return [{'input': [{1, 2}], 'expected': 1}]\n", "JSON"),
        ("x = 1\n", "generate"),
        # NaN не равен себе: кейс с ним не проходит даже эталон, и строгий JSON его не знает.
        (
            "def generate(params, rng):\n    return [{'input': [1], 'expected': float('nan')}]\n",
            "JSON",
        ),
    ],
)
async def test_failing_generator_reports_stderr(source, fragment):
    _, result = await _run(source)
    assert result.exit_code != 0
    with pytest.raises(GeneratorError, match=fragment):
        parse_generator_output(result)


@pytest.mark.parametrize(
    ("stdout", "fragment"),
    [
        ("", "JSON"),
        ("{}", "список"),
        ("[]", "пуст"),
        ('[{"input": [1]}]', "expected"),
        ('[{"input": [1], "expected": 2}, 3]', "expected"),
        # Вход — массив позиционных аргументов entrypoint или объект именованных.
        ('[{"input": 1, "expected": 2}]', "аргумент"),
        ('[{"input": "abc", "expected": 2}]', "аргумент"),
    ],
)
def test_bad_output_shape_is_rejected(stdout, fragment):
    with pytest.raises(GeneratorError, match=fragment):
        parse_generator_output(RunResult(stdout=stdout, stderr="", exit_code=0))


GROUPS = {
    "manual": CaseGroup(weight=2, required=True),
    "random": CaseGroup(weight=1),
    "stress": CaseGroup(weight=1),
}


def _output(*groups: object) -> RunResult:
    cases = [
        {"input": [n], "expected": n} | ({"group": group} if group is not None else {})
        for n, group in enumerate(groups)
    ]
    return RunResult(stdout=json.dumps(cases), stderr="", exit_code=0)


def test_case_keeps_its_declared_group():
    cases = parse_generator_output(_output("manual", "random", "manual"), GROUPS)
    assert [case.group for case in cases] == ["manual", "random", "manual"]


def test_cases_without_declared_groups_have_no_group():
    assert [case.group for case in parse_generator_output(_output(None, None))] == [None, None]


@pytest.mark.parametrize(
    ("groups", "declared", "fragment"),
    [
        # Шаблон объявил группы: кейс без группы не получил бы веса.
        (("manual", None), GROUPS, "без группы"),
        (("manual", "smoke"), GROUPS, "smoke"),
        # Группа в кейсе без объявления в шаблоне: вес группы неизвестен.
        (("manual",), {}, "manual"),
        # Обязательная группа без кейсов сделала бы гейт пустым.
        (("random", "stress"), GROUPS, "manual"),
        ((1,), GROUPS, "строк"),
    ],
)
def test_case_groups_are_checked_against_template(groups, declared, fragment):
    with pytest.raises(GeneratorError, match=fragment):
        parse_generator_output(_output(*groups), declared)


def test_group_required_only_by_settings_may_be_empty():
    # required: null решает настройка при оценке. Группа без required: true — не дефект
    # шаблона, если её кейсов нет в экземпляре.
    groups = {"manual": CaseGroup(weight=1), "stress": CaseGroup(weight=1)}
    assert len(parse_generator_output(_output("manual"), groups)) == 1


def test_timeout_is_reported():
    with pytest.raises(GeneratorError, match="время"):
        parse_generator_output(RunResult(stdout="", stderr="", exit_code=None, timed_out=True))


def test_request_carries_source_in_stdin_not_in_files():
    request = generator_request(GENERATOR, {"n": 1}, 3, LIMITS)
    payload = json.loads(request.stdin)
    assert payload == {"source": GENERATOR, "params": {"n": 1}, "seed": 3}
    assert GENERATOR not in "".join(request.files.values())


# --- Кейсы задачи stdio --------------------------------------------------------------------


def _stdio(*cases: dict) -> list:
    stdout = json.dumps(list(cases))
    return parse_generator_output(RunResult(stdout=stdout, stderr="", exit_code=0), io=STDIO)


def test_stdio_case_carries_stdin_files_and_expected_text():
    [case] = _stdio({"stdin": "GET /\n", "files": {"access.log": "x\n"}, "expected": "1\n"})
    assert (case.stdin, case.files, case.expected) == ("GET /\n", {"access.log": "x\n"}, "1\n")
    assert case.input is None


def test_stdio_case_without_files_has_none():
    assert _stdio({"stdin": "", "expected": "0"})[0].files == {}


@pytest.mark.parametrize(
    ("case", "fragment"),
    [
        ({"expected": "1"}, "stdin"),
        ({"stdin": 5, "expected": "1"}, "stdin"),
        ({"stdin": "1"}, "expected"),
        # Вывод программы — текст: число или список сравнивать не с чем.
        ({"stdin": "1", "expected": 1}, "expected"),
        ({"stdin": "1", "expected": "1", "input": [1]}, "input"),
        ({"stdin": "1", "expected": "1", "files": ["a"]}, "files"),
        ({"stdin": "1", "expected": "1", "files": {"a.txt": 1}}, "files"),
        ({"stdin": "1", "expected": "1", "files": {"../etc/x": "1"}}, "имя"),
        ({"stdin": "1", "expected": "1", "files": {".hidden": "1"}}, "имя"),
        ({"stdin": "1", "expected": "1", "files": {"dir/a.txt": "1"}}, "имя"),
    ],
)
def test_bad_stdio_case_is_rejected(case, fragment):
    with pytest.raises(GeneratorError, match=fragment):
        _stdio(case)


def test_function_case_must_not_carry_stdin():
    stdout = json.dumps([{"input": [1], "stdin": "1", "expected": 1}])
    with pytest.raises(GeneratorError, match="stdin"):
        parse_generator_output(RunResult(stdout=stdout, stderr="", exit_code=0), io=FUNCTION)
