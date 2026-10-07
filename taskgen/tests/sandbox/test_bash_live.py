"""Окружение bash в реальном Piston: версии из описания и программа stdio на Bash."""

import re

import pytest

from taskgen.contracts import (
    IOKind,
    OutputCheck,
    RunLimits,
    RunRequest,
    TestCase,
    TestRunRequest,
)
from taskgen.sandbox.registry import RuntimeRegistry

pytestmark = pytest.mark.integration

LIMITS = RunLimits(timeout_ms=3000, memory_bytes=256 * 1024 * 1024)
PROBE = (
    'echo "bash $BASH_VERSION"\n'
    "sort --version | head -n 1\n"
    "grep --version | head -n 1\n"
    "sed --version | head -n 1\n"
    "awk -W version 2>&1 | head -n 1\n"
)


async def test_description_names_the_versions_the_sandbox_has(piston):
    spec = RuntimeRegistry.load().get("bash")
    result = await piston.run(
        RunRequest(runtime="bash", files={spec.source_file: PROBE}, limits=LIMITS)
    )
    assert result.exit_code == 0, result.stderr
    bash, coreutils, grep, sed, awk = result.stdout.splitlines()
    assert bash.startswith(f"bash {spec.version.rsplit('.', 1)[0]}")
    found = {
        "coreutils": re.search(r"coreutils\) (\S+)", coreutils)[1],
        "grep": re.search(r"grep\) (\S+)", grep)[1],
        "sed": re.search(r"sed\) (\S+)", sed)[1],
        "mawk": re.search(r"mawk (\S+)", awk)[1],
    }
    for tool, version in found.items():
        assert f"{tool} {version}" in spec.extras, (tool, version, spec.extras)


async def test_bash_program_reads_first_line_then_the_rest(piston):
    solution = "read -r n\nawk -v n=\"$n\" '$1 >= n { c++ } END { print c + 0 }'\n"
    cases = [
        TestCase(stdin="2\n1\n2\n3\n", expected="2"),
        TestCase(stdin="9\n1\n", expected="0"),
    ]
    request = TestRunRequest(
        runtime="bash",
        io=IOKind.STDIO,
        solution=solution,
        cases=cases,
        check=OutputCheck(mode="tokens"),
        limits=LIMITS,
    )

    report = await piston.run_tests(request)

    assert [o.passed for o in report.outcomes] == [True, True], report
