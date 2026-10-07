"""Статические анализаторы: драйвер анализа и разбор его отчёта сервисом.

Здесь драйвер исполняется локально через LocalSandbox с инструментами из группы dev проекта.
Настоящий запуск в окружении analyzers Piston проверяет test_analyzers_live.py.
"""

import shutil
import textwrap

import pytest

from taskgen.contracts import RunResult
from taskgen.evaluation.analyzers import ANALYSIS_TOOL, SandboxAnalyzer
from taskgen.evaluation.metrics import load_rubric
from taskgen.sandbox.errors import SandboxServiceError
from tests.generation.fakes import LocalSandbox

PY_SIMPLE = textwrap.dedent(
    '''
    """Модуль."""


    def add(a: int, b: int) -> int:
        """Сумма."""
        return a + b
    '''
)

PY_TANGLED = textwrap.dedent(
    """
    class badName:
        def Method(self, x, y, z):
            if x:
                for i in range(y):
                    if i % 2:
                        if z > 42:
                            return 1
                        elif z < 3:
                            return 2
                    elif i % 3:
                        while z:
                            z -= 1
                            if z == 7:
                                break
            return 0
    """
)

CPP_SIMPLE = textwrap.dedent(
    """
    int add(int a, int b) { return a + b; }
    """
)

CPP_TANGLED = textwrap.dedent(
    """
    class bad_name { public: int x; };
    int f(int a, int b, int c, int d, int e, int g, int h, int k) {
        int s = 0;
        for (int i = 0; i < a; ++i)
            for (int j = 0; j < b; ++j)
                if (i % 2) { if (j % 3) { if (c) s++; else s--; } }
                else if (d) { while (e--) { if (g && h) s += k; } }
        return s;
    }
    """
)


def analyzer(sandbox=None, timeout: float = 30.0) -> SandboxAnalyzer:
    return SandboxAnalyzer(
        sandbox or LocalSandbox(), timeout_seconds=timeout, memory_bytes=256 * 1024 * 1024
    )


async def by_tool(code: str, language: str) -> dict:
    return {report.tool: report for report in await analyzer().analyze(code, language)}


async def test_analysis_runs_in_the_sandbox_not_in_the_service():
    sandbox = LocalSandbox()
    await analyzer(sandbox, timeout=12).analyze(PY_SIMPLE, "python")
    [run] = sandbox.runs
    assert run.runtime == "analyzers"
    assert list(run.files) == ["taskgen_analyze.py", "solution.py"]
    assert run.files["solution.py"] == PY_SIMPLE
    assert run.args == ["python", "solution.py", "12"]
    # Песочнице хватает времени на бюджет инструментов и вывод отчёта.
    assert run.limits.timeout_ms > 12_000


async def test_ruff_counts_findings_by_rule_family():
    report = (await by_tool(PY_TANGLED, "python"))["ruff"]
    assert report.metrics["N"] >= 2  # badName, Method
    assert report.metrics["ANN"] >= 4
    assert report.metrics["total"] == sum(
        report.metrics[k] for k in ("C901", "N", "ANN", "D", "PLR")
    )
    assert any(finding["code"].startswith("N") for finding in report.findings)


async def test_ruff_finds_nothing_in_clean_code():
    assert (await by_tool(PY_SIMPLE, "python"))["ruff"].metrics["total"] == 0


async def test_radon_separates_simple_and_tangled_code():
    simple = (await by_tool(PY_SIMPLE, "python"))["radon"]
    tangled = (await by_tool(PY_TANGLED, "python"))["radon"]
    assert simple.metrics["cc_max"] == 1
    assert tangled.metrics["cc_max"] > 5
    assert tangled.metrics["mi"] < simple.metrics["mi"]
    assert simple.metrics["sloc"] > 0


async def test_radon_measures_program_without_functions():
    # Программа задачи stdio может не объявлять ни одной функции.
    report = (await by_tool("import sys\nprint(len(sys.stdin.read()))\n", "python"))["radon"]
    assert "cc_max" not in report.metrics
    assert report.metrics["sloc"] == 2


async def test_radon_reports_syntax_error_instead_of_failing():
    report = (await by_tool("def broken(:\n", "python"))["radon"]
    assert report.metrics == {}
    assert report.findings


async def test_annotation_ratio_skips_self_and_counts_returns():
    code = textwrap.dedent(
        """
        class A:
            def m(self, x: int, y) -> int:
                return x

        def f(a, b: str):
            pass
        """
    )
    report = (await by_tool(code, "python"))["annotations"]
    # Места: x, y, return m, a, b, return f — аннотированы x, return m, b.
    assert report.metrics["slots"] == 6
    assert report.metrics["ratio"] == pytest.approx(3 / 6)


async def test_annotation_ratio_without_functions_is_absent():
    assert (await by_tool("x = 1\n", "python"))["annotations"].metrics == {"slots": 0}


async def test_deeply_nested_code_is_a_note_not_a_service_crash():
    # ast.parse бросает RecursionError или MemoryError, а не SyntaxError: docs/taskgen/security.md.
    for code in ("x = " + "-" * 10_000 + "1\n", "x = a" + ".b" * 10_000 + "\n"):
        reports = await by_tool(code, "python")
        assert "вложен" in reports["annotations"].findings[0]["note"]


async def test_lizard_measures_cpp_functions():
    simple = (await by_tool(CPP_SIMPLE, "cpp"))["lizard"]
    tangled = (await by_tool(CPP_TANGLED, "cpp"))["lizard"]
    assert simple.metrics["functions"] == 1
    assert simple.metrics["ccn_max"] == 1
    assert tangled.metrics["ccn_max"] > 5
    assert tangled.metrics["params_max"] == 8
    assert tangled.metrics["nloc"] > simple.metrics["nloc"]


@pytest.mark.skipif(shutil.which("clang-tidy") is None, reason="clang-tidy не установлен")
async def test_clang_tidy_reports_cognitive_complexity_and_naming():
    report = (await by_tool(CPP_TANGLED, "cpp"))["clang_tidy"]
    assert report.metrics["cognitive_max"] > 5
    assert report.metrics["naming"] >= 1


@pytest.mark.skipif(shutil.which("cppcheck") is not None, reason="cppcheck установлен")
async def test_missing_tool_gives_empty_report_with_note():
    report = (await by_tool(CPP_SIMPLE, "cpp"))["cppcheck"]
    assert report.metrics == {}
    assert "не найден" in report.findings[0]["note"]


async def test_language_without_analyzers_is_not_sent_to_the_sandbox():
    sandbox = LocalSandbox()
    assert await analyzer(sandbox).analyze("echo hi", "bash") == []
    assert await analyzer(sandbox).analyze(PY_SIMPLE, "cobol") == []
    assert sandbox.runs == []


async def test_language_alias_picks_the_analyzers():
    tools = {report.tool for report in await analyzer().analyze(PY_SIMPLE, "python3")}
    assert tools == {"ruff", "radon", "annotations"}


async def test_runtime_on_a_language_driver_gets_its_analyzers_and_rubric():
    # Ответ на задание ML приходит с языком окружения python-ml: это Python, и без
    # анализаторов балл качества заданий ML терялся молча.
    tools = {report.tool for report in await analyzer().analyze(PY_SIMPLE, "python-ml")}
    assert tools == {"ruff", "radon", "annotations"}
    assert load_rubric("python-ml") == load_rubric("python")


class _Answering:
    def __init__(self, result=None, error=None) -> None:
        self.result, self.error = result, error

    async def run(self, request):
        if self.error is not None:
            raise self.error
        return self.result


@pytest.mark.parametrize(
    ("sandbox", "fragment"),
    [
        (_Answering(error=SandboxServiceError("Piston недоступен")), "не запущен"),
        (_Answering(RunResult(stdout="", stderr="", exit_code=None, timed_out=True)), "время"),
        (_Answering(RunResult(stdout="", stderr="Traceback", exit_code=1)), "кодом 1"),
        (_Answering(RunResult(stdout="{not json", stderr="", exit_code=0)), "не список"),
        (_Answering(RunResult(stdout='{"tool": 1}', stderr="", exit_code=0)), "не список"),
    ],
)
async def test_failed_analysis_is_one_note_and_the_answer_survives(sandbox, fragment):
    [report] = await analyzer(sandbox).analyze(PY_SIMPLE, "python")
    assert report.tool == ANALYSIS_TOOL and report.metrics == {}
    assert fragment in report.findings[0]["note"]


@pytest.mark.parametrize(("language", "code"), [("python", PY_TANGLED), ("cpp", CPP_TANGLED)])
async def test_rubric_and_analyzers_agree_on_metric_keys(language, code):
    # Рубрика описывает каждую метрику, которую отдают анализаторы, и наоборот.
    # Инструмент, которого нет на машине, отдаёт пустой отчёт; его ключи не ожидаются.
    rubric = load_rubric(language)
    reports = await analyzer().analyze(code, language)
    emitted = {f"{report.tool}.{name}" for report in reports for name in report.metrics}
    present = {f"{report.tool}." for report in reports if report.metrics}
    expected = {key for key in rubric.metrics if key.startswith(tuple(present))}
    assert emitted == expected
