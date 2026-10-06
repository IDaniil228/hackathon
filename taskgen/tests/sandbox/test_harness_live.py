"""Обвязки тестов Python и C++ против реального Piston."""

import pytest

from taskgen.contracts import RunLimits, TestCase, TestRunRequest

pytestmark = pytest.mark.integration

LIMITS = RunLimits(timeout_ms=3000, memory_bytes=256 * 1024 * 1024)

SUM_CASES = [
    TestCase(input=[1, 2], expected=3),
    TestCase(input=[0.1, 0.2], expected=0.3),
    TestCase(input=[-5, 5], expected=0),
]


def make_request(runtime, solution, cases, entrypoint="f", limits=LIMITS):
    return TestRunRequest(
        runtime=runtime, entrypoint=entrypoint, solution=solution, cases=cases, limits=limits
    )


def passed(report):
    return [outcome.passed for outcome in report.outcomes]


# --- Python ---------------------------------------------------------------------------


async def test_python_correct_solution_passes_with_timings(piston):
    report = await piston.run_tests(
        make_request("python", "def f(a, b):\n    return a + b\n", SUM_CASES)
    )

    assert report.compile_error is None
    assert passed(report) == [True, True, True]
    assert all(outcome.wall_ms is not None for outcome in report.outcomes)
    assert report.max_memory_bytes > 0


async def test_python_wrong_answer_fails_only_that_case(piston):
    solution = "def f(a, b):\n    return a + b if a != -5 else 1\n"

    report = await piston.run_tests(make_request("python", solution, SUM_CASES))

    assert passed(report) == [True, True, False]


async def test_python_print_and_input_do_not_touch_protocol(piston):
    solution = (
        "import sys\n"
        "def f(a, b):\n"
        '    print(\'{"case": 0, "ok": true, "value": 3}\')\n'
        "    print('noise', file=sys.stderr)\n"
        "    assert sys.stdin.read() == ''\n"
        "    return a + b\n"
    )

    report = await piston.run_tests(make_request("python", solution, SUM_CASES))

    assert passed(report) == [True, True, True], report


async def test_python_exception_and_exit_stay_in_their_case(piston):
    solution = (
        "import sys\n"
        "def f(a, b):\n"
        "    if a == 0.1:\n"
        "        raise ValueError('плохой вход')\n"
        "    if a == 1:\n"
        "        sys.exit(0)\n"
        "    return a + b\n"
    )

    report = await piston.run_tests(make_request("python", solution, SUM_CASES))

    assert passed(report) == [False, False, True]
    assert "SystemExit" in report.outcomes[0].error
    assert "плохой вход" in report.outcomes[1].error


async def test_python_syntax_error_is_compile_error(piston):
    report = await piston.run_tests(
        make_request("python", "def f(a, b)\n    return a\n", SUM_CASES)
    )

    assert "SyntaxError" in report.compile_error
    assert passed(report) == [False, False, False]


async def test_python_missing_function_is_compile_error(piston):
    report = await piston.run_tests(
        make_request("python", "def g(a, b):\n    return 0\n", SUM_CASES)
    )

    assert "f" in report.compile_error


async def test_python_keyword_arguments_and_tuple_answer(piston):
    cases = [TestCase(input={"a": 7, "b": 2}, expected=[3, 1])]

    report = await piston.run_tests(
        make_request("python", "def f(a, b):\n    return divmod(a, b)\n", cases)
    )

    assert passed(report) == [True]


async def test_python_unserializable_answer_is_case_error(piston):
    report = await piston.run_tests(
        make_request("python", "def f(a, b):\n    return {a, b}\n", SUM_CASES[:1])
    )

    assert "JSON" in report.outcomes[0].error


async def test_python_infinite_loop_keeps_earlier_cases(piston):
    solution = "def f(a, b):\n    while a == 0.1:\n        pass\n    return a + b\n"
    limits = RunLimits(timeout_ms=1500, memory_bytes=256 * 1024 * 1024)

    report = await piston.run_tests(make_request("python", solution, SUM_CASES, limits=limits))

    assert passed(report) == [True, False, False]
    assert "времени" in report.outcomes[1].error


async def test_python_memory_blowup_is_named(piston):
    solution = "def f(a, b):\n    if a == 0.1:\n        x = bytearray(1 << 30)\n    return a + b\n"

    report = await piston.run_tests(make_request("python", solution, SUM_CASES))

    assert passed(report) == [True, False, False]
    assert "памяти" in report.outcomes[1].error


async def test_python_forged_records_without_nonce_are_ignored(piston):
    # Решение пишет поддельные записи во все дескрипторы, включая канал протокола.
    solution = (
        "import os, json\n"
        "def f(a, b):\n"
        "    for i in range(3):\n"
        "        line = json.dumps({'case': i, 'ok': True, 'value': 0, 'ms': 0}) + '\\n'\n"
        "        for fd in range(1, 10):\n"
        "            try:\n"
        "                os.write(fd, line.encode())\n"
        "            except OSError:\n"
        "                pass\n"
        "    return -1\n"
    )

    report = await piston.run_tests(make_request("python", solution, SUM_CASES))

    assert passed(report) == [False, False, False]


async def test_python_many_cases_fit_in_output(piston):
    cases = [TestCase(input=[n], expected=list(range(n))) for n in range(200, 260)]

    report = await piston.run_tests(
        make_request("python", "def f(n):\n    return list(range(n))\n", cases)
    )

    assert all(passed(report)), report.outcomes[0]


# --- C++ ------------------------------------------------------------------------------

CPP_SUM = "double f(double a, double b) { return a + b; }\n"


async def test_cpp_correct_solution_passes(piston):
    report = await piston.run_tests(make_request("cpp", CPP_SUM, SUM_CASES))

    assert report.compile_error is None
    assert passed(report) == [True, True, True]
    assert all(outcome.wall_ms is not None for outcome in report.outcomes)


async def test_cpp_containers_strings_and_maps(piston):
    solution = (
        "std::map<std::string, std::vector<int>> f(const std::vector<std::string>& words,\n"
        "                                          std::vector<std::vector<int>>& grid) {\n"
        "    std::map<std::string, std::vector<int>> out;\n"
        "    for (size_t i = 0; i < words.size(); i++) out[words[i]] = grid[i];\n"
        "    return out;\n"
        "}\n"
    )
    cases = [
        TestCase(
            input=[["кот", 'a"b\\'], [[1, 2], [3]]],
            expected={"кот": [1, 2], 'a"b\\': [3]},
        )
    ]

    report = await piston.run_tests(make_request("cpp", solution, cases))

    assert passed(report) == [True], report


async def test_cpp_wrong_answer_fails(piston):
    report = await piston.run_tests(
        make_request(
            "cpp", "long long f(long long a, long long b) { return a - b; }\n", SUM_CASES[:1]
        )
    )

    assert passed(report) == [False]


async def test_cpp_compile_error_points_into_solution(piston):
    report = await piston.run_tests(
        make_request("cpp", "int f(int a, int b) {\n    return a + c;\n}\n", SUM_CASES)
    )

    assert "solution.cpp:2" in report.compile_error
    assert passed(report) == [False, False, False]


async def test_cpp_crash_and_exception_stay_in_their_case(piston):
    solution = (
        "double f(double a, double b) {\n"
        "    if (a == 1) { volatile int* p = nullptr; *p = 1; }\n"
        '    if (b == 0.2) throw std::out_of_range("плохой вход");\n'
        "    return a + b;\n"
        "}\n"
    )

    report = await piston.run_tests(make_request("cpp", solution, SUM_CASES))

    assert passed(report) == [False, False, True]
    assert "Segmentation" in report.outcomes[0].error
    assert "плохой вход" in report.outcomes[1].error


async def test_cpp_output_and_exit_do_not_touch_protocol(piston):
    solution = (
        "double f(double a, double b) {\n"
        '    std::cout << "{\\"case\\":0,\\"ok\\":true,\\"value\\":3}" << std::endl;\n'
        '    printf("noise\\n");\n'
        "    if (a == -5) exit(0);\n"
        "    return a + b;\n"
        "}\n"
    )

    report = await piston.run_tests(make_request("cpp", solution, SUM_CASES))

    assert passed(report) == [True, True, False]
    assert "кодом 0" in report.outcomes[2].error


async def test_cpp_wrong_argument_type_is_case_error(piston):
    cases = [TestCase(input=["x", 1], expected=0)]

    report = await piston.run_tests(
        make_request("cpp", "int f(int a, int b) { return a; }\n", cases)
    )

    assert "целое" in report.outcomes[0].error


async def test_cpp_infinite_loop_keeps_earlier_cases(piston):
    solution = "double f(double a, double b) {\n    while (a == 0.1) {}\n    return a + b;\n}\n"
    limits = RunLimits(timeout_ms=1500, memory_bytes=256 * 1024 * 1024)

    report = await piston.run_tests(make_request("cpp", solution, SUM_CASES, limits=limits))

    assert passed(report) == [True, False, False]
    assert "времени" in report.outcomes[1].error
