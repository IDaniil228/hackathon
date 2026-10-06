"""Окружение python-ml в реальном Piston; ставится скриптом taskgen/sandbox/piston-packages.sh."""

import pytest

from taskgen.contracts import RunLimits, RunRequest, TestCase, TestRunRequest
from taskgen.sandbox.registry import RuntimeRegistry

pytestmark = pytest.mark.integration

# Импорт torch занимает сотни мегабайт и несколько секунд, лимиты обычного python ему малы.
LIMITS = RunLimits(timeout_ms=20000, memory_bytes=1024 * 1024 * 1024)


async def test_interpreter_lives_in_its_own_package(piston):
    code = (
        "import sys, torch, sklearn, numpy\n"
        "print(sys.prefix)\n"
        "print(torch.__version__, torch.get_num_threads())\n"
    )

    result = await piston.run(
        RunRequest(runtime="python-ml", files={"main.py": code}, limits=LIMITS)
    )

    assert result.exit_code == 0, result.stderr
    prefix, versions = result.stdout.splitlines()
    assert prefix.endswith("/python-ml/3.12.0")
    assert versions.endswith("+cpu 1")


async def test_installed_libraries_match_environment_description(piston):
    # Кандидат видит версии из requirements.txt; установленный пакет обязан им соответствовать.
    libraries = RuntimeRegistry.load().libraries("python-ml")
    code = (
        "import sys\nfrom importlib.metadata import version\n"
        "for name in sys.argv[1:]:\n    print(name, version(name).split('+')[0])\n"
    )

    result = await piston.run(
        RunRequest(
            runtime="python-ml",
            files={"main.py": code},
            args=[name for name, _ in libraries],
            limits=LIMITS,
        )
    )

    assert result.exit_code == 0, result.stderr
    assert result.stdout.split("\n")[:-1] == [f"{name} {ver}" for name, ver in libraries]


async def test_ml_solution_answers_with_numpy_and_torch_values(piston):
    solution = (
        "import numpy as np\n"
        "import torch\n"
        "from sklearn.linear_model import LinearRegression\n"
        "\n"
        "def fit(xs, ys):\n"
        "    model = LinearRegression().fit(np.array(xs).reshape(-1, 1), np.array(ys))\n"
        "    slope = torch.tensor(model.coef_)[0]\n"
        "    return [slope, np.float64(model.intercept_)]\n"
    )
    cases = [
        TestCase(input=[[0, 1, 2, 3], [1, 3, 5, 7]], expected=[2.0, 1.0]),
        TestCase(input=[[0, 1], [5, 5]], expected=[0.0, 5.0]),
    ]

    report = await piston.run_tests(
        TestRunRequest(
            runtime="python-ml", entrypoint="fit", solution=solution, cases=cases, limits=LIMITS
        )
    )

    assert report.compile_error is None
    assert [o.passed for o in report.outcomes] == [True, True], report
