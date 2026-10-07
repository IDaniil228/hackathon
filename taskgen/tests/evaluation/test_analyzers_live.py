"""Анализаторы в окружении analyzers настоящего Piston; ставится piston-packages.sh."""

import json

import pytest

from taskgen.config import Settings
from taskgen.evaluation.analyzers import SandboxAnalyzer
from taskgen.sandbox.piston import PistonRunner
from tests.evaluation.test_analyzers import CPP_TANGLED, PY_TANGLED

pytestmark = pytest.mark.integration


@pytest.fixture
async def analyzer():
    settings = Settings()
    runner = PistonRunner(settings.sandbox)
    yield SandboxAnalyzer(
        runner,
        timeout_seconds=settings.scoring.analyzer_timeout_seconds,
        memory_bytes=settings.sandbox.run_memory_limit_bytes,
    )
    await runner.aclose()


async def test_every_python_tool_reports_metrics(analyzer):
    reports = {r.tool: r for r in await analyzer.analyze(PY_TANGLED, "python")}
    assert set(reports) == {"ruff", "radon", "annotations"}
    assert reports["ruff"].metrics["N"] >= 2
    assert reports["radon"].metrics["cc_max"] > 5
    assert reports["annotations"].metrics["slots"] > 0


async def test_every_cpp_tool_reports_metrics(analyzer):
    code = "#include <bits/stdc++.h>\n" + CPP_TANGLED
    reports = {r.tool: r for r in await analyzer.analyze(code, "cpp")}
    assert set(reports) == {"lizard", "clang_tidy", "cppcheck"}
    assert reports["lizard"].metrics["params_max"] == 8
    assert reports["clang_tidy"].metrics["cognitive_max"] > 5
    assert reports["clang_tidy"].metrics["naming"] >= 1
    assert "issues" in reports["cppcheck"].metrics


async def test_include_of_a_service_host_file_reads_nothing_from_the_host(analyzer, tmp_path):
    # Анализаторы в процессе сервиса раскрывали макрос из файла хоста в имени переменной,
    # и cppcheck возвращал его в замечании: docs/taskgen/security.md.
    secret = tmp_path / "secret.h"
    secret.write_text("#define DB_PASSWORD Hunter2xyz\n", encoding="utf-8")
    code = (
        f'#include "{secret}"\n'
        "#define CAT2(a, b) a##b\n#define CAT(a, b) CAT2(a, b)\n"
        "int solve(int a) { int CAT(Leak_, DB_PASSWORD) = a; return 0; }\n"
    )
    reports = await analyzer.analyze(code, "cpp")
    dumped = json.dumps([r.model_dump() for r in reports], ensure_ascii=False)
    assert "Hunter2xyz" not in dumped
    assert {r.tool for r in reports} == {"lizard", "clang_tidy", "cppcheck"}


async def test_deeply_nested_python_does_not_reach_the_service(analyzer):
    # 10 000 уровней хватает, чтобы ast.parse упал, и запрос помещается в лимит тела Piston.
    for code in ("x = " + "-" * 10_000 + "1\n", "x = a" + ".b" * 10_000 + "\n"):
        reports = {r.tool: r for r in await analyzer.analyze(code, "python")}
        assert "вложен" in reports["annotations"].findings[0]["note"]
