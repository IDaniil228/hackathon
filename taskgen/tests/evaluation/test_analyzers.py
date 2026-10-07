import shutil
import textwrap

import pytest

from taskgen.evaluation.analyzers import (
    AnnotationAnalyzer,
    ClangTidyAnalyzer,
    CppcheckAnalyzer,
    LizardAnalyzer,
    RadonAnalyzer,
    RuffAnalyzer,
    default_analyzers,
    parse_cppcheck_xml,
    run_analyzers,
)
from taskgen.evaluation.metrics import load_rubric

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


def test_ruff_counts_findings_by_rule_family():
    report = RuffAnalyzer().analyze(PY_TANGLED, "python")
    assert report.tool == "ruff"
    assert report.metrics["N"] >= 2  # badName, Method
    assert report.metrics["ANN"] >= 4
    assert report.metrics["total"] == sum(
        report.metrics[k] for k in ("C901", "N", "ANN", "D", "PLR")
    )
    assert any(finding["code"].startswith("N") for finding in report.findings)


def test_ruff_finds_nothing_in_clean_code():
    report = RuffAnalyzer().analyze(PY_SIMPLE, "python")
    assert report.metrics["total"] == 0


def test_radon_separates_simple_and_tangled_code():
    simple = RadonAnalyzer().analyze(PY_SIMPLE, "python")
    tangled = RadonAnalyzer().analyze(PY_TANGLED, "python")
    assert simple.metrics["cc_max"] == 1
    assert tangled.metrics["cc_max"] > 5
    assert tangled.metrics["mi"] < simple.metrics["mi"]
    assert simple.metrics["sloc"] > 0


def test_radon_measures_program_without_functions():
    # Программа задачи stdio может не объявлять ни одной функции.
    report = RadonAnalyzer().analyze("import sys\nprint(len(sys.stdin.read()))\n", "python")
    assert "cc_max" not in report.metrics
    assert report.metrics["sloc"] == 2


def test_radon_reports_syntax_error_instead_of_failing():
    report = RadonAnalyzer().analyze("def broken(:\n", "python")
    assert report.metrics == {}
    assert report.findings


def test_annotation_ratio_skips_self_and_counts_returns():
    code = textwrap.dedent(
        """
        class A:
            def m(self, x: int, y) -> int:
                return x

        def f(a, b: str):
            pass
        """
    )
    report = AnnotationAnalyzer().analyze(code, "python")
    # Места: x, y, return m, a, b, return f — аннотированы x, return m, b.
    assert report.metrics["slots"] == 6
    assert report.metrics["ratio"] == pytest.approx(3 / 6)


def test_annotation_ratio_without_functions_is_absent():
    report = AnnotationAnalyzer().analyze("x = 1\n", "python")
    assert report.metrics == {"slots": 0}


def test_lizard_measures_cpp_functions():
    simple = LizardAnalyzer().analyze(CPP_SIMPLE, "cpp")
    tangled = LizardAnalyzer().analyze(CPP_TANGLED, "cpp")
    assert simple.metrics["functions"] == 1
    assert simple.metrics["ccn_max"] == 1
    assert tangled.metrics["ccn_max"] > 5
    assert tangled.metrics["params_max"] == 8
    assert tangled.metrics["nloc"] > simple.metrics["nloc"]


@pytest.mark.skipif(shutil.which("clang-tidy") is None, reason="clang-tidy не установлен")
def test_clang_tidy_reports_cognitive_complexity_and_naming():
    report = ClangTidyAnalyzer().analyze(CPP_TANGLED, "cpp")
    assert report.metrics["cognitive_max"] > 5
    assert report.metrics["naming"] >= 1


def test_missing_tool_gives_empty_report_with_note():
    for analyzer in (
        ClangTidyAnalyzer(binary="no-such-clang-tidy-binary"),
        CppcheckAnalyzer(binary="no-such-cppcheck-binary"),
    ):
        report = analyzer.analyze(CPP_SIMPLE, "cpp")
        assert report.metrics == {}
        assert "не найден" in report.findings[0]["note"]


def test_cppcheck_xml_counts_only_real_issues():
    xml = """<?xml version="1.0"?>
    <results version="2"><errors>
      <error id="uninitvar" severity="error" msg="Uninitialized variable: x"/>
      <error id="passedByValue" severity="performance" msg="Pass by reference"/>
      <error id="missingIncludeSystem" severity="information" msg="Include not found"/>
    </errors></results>"""
    count, findings = parse_cppcheck_xml(xml)
    assert count == 2
    assert {finding["code"] for finding in findings} == {"uninitvar", "passedByValue"}


def test_run_analyzers_picks_by_language_alias():
    reports = run_analyzers(default_analyzers(), PY_SIMPLE, "python3")
    assert {report.tool for report in reports} == {"ruff", "radon", "annotations"}
    assert run_analyzers(default_analyzers(), PY_SIMPLE, "cobol") == []


@pytest.mark.parametrize(("language", "code"), [("python", PY_TANGLED), ("cpp", CPP_TANGLED)])
def test_rubric_and_analyzers_agree_on_metric_keys(language, code):
    # Рубрика описывает каждую метрику, которую отдают анализаторы, и наоборот.
    # Инструмент, которого нет на машине, отдаёт пустой отчёт; его ключи не ожидаются.
    rubric = load_rubric(language)
    reports = run_analyzers(default_analyzers(), code, language)
    emitted = {f"{report.tool}.{name}" for report in reports for name in report.metrics}
    present = {f"{report.tool}." for report in reports if report.metrics}
    expected = {key for key in rubric.metrics if key.startswith(tuple(present))}
    assert emitted == expected
