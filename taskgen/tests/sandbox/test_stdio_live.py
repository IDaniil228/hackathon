"""Задачи stdio против реального Piston: программа на каждом кейсе, вход в stdin и файлах."""

import pytest

from taskgen.contracts import (
    Checker,
    IOKind,
    OutputCheck,
    RunLimits,
    TestCase,
    TestRunRequest,
)

pytestmark = pytest.mark.integration

# Проверяльщик принимает любой порядок чисел: ответ — множество, а не строка.
SET_CHECKER = Checker(
    runtime="python",
    code=(
        "import sys\n"
        "_, output, answer = (open(name).read().split() for name in sys.argv[1:4])\n"
        "sys.exit(0 if sorted(output) == sorted(answer) else 1)\n"
    ),
)

LIMITS = RunLimits(timeout_ms=3000, memory_bytes=256 * 1024 * 1024)


def make_request(runtime, solution, cases, mode="tokens"):
    return TestRunRequest(
        runtime=runtime,
        io=IOKind.STDIO,
        solution=solution,
        cases=cases,
        check=OutputCheck(mode=mode),
        limits=LIMITS,
    )


def passed(report):
    return [outcome.passed for outcome in report.outcomes]


SUM_CASES = [
    TestCase(stdin="1 2\n", expected="3\n"),
    TestCase(stdin="10\n-4\n", expected="6"),
    TestCase(stdin="", expected="0"),
]


async def test_python_program_reads_stdin_and_prints_answer(piston):
    solution = "import sys\nprint(sum(int(t) for t in sys.stdin.read().split()))\n"

    report = await piston.run_tests(make_request("python", solution, SUM_CASES))

    assert report.compile_error is None
    assert passed(report) == [True, True, True], report
    assert report.max_wall_ms is not None and report.max_memory_bytes


async def test_python_program_reads_case_files_next_to_it(piston):
    solution = (
        "import sys\n"
        "name = sys.stdin.read().strip()\n"
        "with open(name, encoding='utf-8') as f:\n"
        "    print(len(f.read().splitlines()))\n"
    )
    cases = [
        TestCase(stdin="a.log\n", files={"a.log": "x\ny\nz\n"}, expected="3"),
        TestCase(stdin="b.log\n", files={"b.log": "only\n"}, expected="1"),
    ]

    report = await piston.run_tests(make_request("python", solution, cases))

    assert passed(report) == [True, True], report


async def test_runtime_error_fails_only_its_case(piston):
    solution = (
        "import sys\n"
        "values = sys.stdin.read().split()\n"
        "print(sum(int(v) for v in values))\n"
        "if not values:\n"
        "    sys.exit(3)\n"
    )

    report = await piston.run_tests(make_request("python", solution, SUM_CASES))

    assert passed(report) == [True, True, False]
    assert "3" in report.outcomes[2].error


async def test_exact_mode_sees_the_trailing_newline(piston):
    solution = "print('ok')\n"
    cases = [TestCase(stdin="", expected="ok\n"), TestCase(stdin="", expected="ok")]

    report = await piston.run_tests(make_request("python", solution, cases, mode="exact"))

    assert passed(report) == [True, False]


async def test_cpp_program_is_compiled_and_run_per_case(piston):
    solution = (
        "#include <iostream>\n"
        "int main() { long long a = 0, s = 0; while (std::cin >> a) s += a; "
        "std::cout << s << '\\n'; }\n"
    )

    report = await piston.run_tests(make_request("cpp", solution, SUM_CASES))

    assert report.compile_error is None
    assert passed(report) == [True, True, True], report


async def test_template_checker_runs_in_piston(piston):
    solution = "import sys\nprint(*sorted(set(sys.stdin.read().split()), reverse=True))\n"
    cases = [
        TestCase(stdin="3 1 2 3\n", expected="1 2 3\n"),
        TestCase(stdin="5 5\n", expected="5 6\n"),
    ]
    request = make_request("python", solution, cases).model_copy(
        update={"check": OutputCheck(mode="checker", checker=SET_CHECKER)}
    )

    report = await piston.run_tests(request)

    assert passed(report) == [True, False], report
    assert report.outcomes[1].error == "неверный ответ"


async def test_cpp_compile_error_is_reported_once(piston):
    report = await piston.run_tests(make_request("cpp", "int main( {", SUM_CASES))

    assert report.compile_error
    assert passed(report) == [False, False, False]
