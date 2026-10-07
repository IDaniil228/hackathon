"""Статические анализаторы кода кандидата (протокол Analyzer).

Каждый анализатор — один инструмент. Код записывается во временную директорию, инструмент
читает файл и не исполняет его. Ключи метрик описаны в рубриках каталога rubrics: ключ
"инструмент.метрика" в рубрике совпадает с AnalysisReport.tool и ключом в metrics.
Инструмента нет на машине или он не уложился во время — отчёт пустой, с пометкой в findings.
"""

from __future__ import annotations

import ast
import csv
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from statistics import fmean
from typing import Any

from taskgen.contracts import AnalysisReport, Analyzer
from taskgen.evaluation.languages import canonical_language

_PYTHON = frozenset({"python"})
_CPP = frozenset({"cpp"})
_SOURCE_NAME = {"python": "solution.py", "cpp": "solution.cpp"}
DEFAULT_TIMEOUT_SECONDS = 30.0


def _note(tool: str, text: str) -> AnalysisReport:
    return AnalysisReport(tool=tool, findings=[{"note": text}])


class _ToolMissingError(Exception):
    pass


class _FileAnalyzer:
    """Общая часть: временная директория, запуск процесса и пометки об отказе."""

    tool: str
    languages: frozenset[str]

    def __init__(self, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.timeout_seconds = timeout_seconds

    def analyze(self, code: str, language: str) -> AnalysisReport:
        name = canonical_language(language)
        if name not in self.languages:
            return _note(self.tool, f"язык {language} не поддерживается")
        with tempfile.TemporaryDirectory(prefix="taskgen-analyze-") as directory:
            path = Path(directory) / _SOURCE_NAME[name]
            path.write_text(code, encoding="utf-8")
            try:
                return self._analyze_file(path)
            except _ToolMissingError as error:
                return _note(self.tool, f"инструмент {error} не найден")
            except subprocess.TimeoutExpired:
                return _note(self.tool, f"анализ не уложился в {self.timeout_seconds} с")

    def _run(self, command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if shutil.which(command[0]) is None:
            raise _ToolMissingError(command[0])
        return subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=self.timeout_seconds,
            check=False,
        )

    def _analyze_file(self, path: Path) -> AnalysisReport:
        raise NotImplementedError


def _ruff_binary() -> str:
    try:
        from ruff.__main__ import find_ruff_bin
    except ImportError:
        return "ruff"
    return str(find_ruff_bin())


class RuffAnalyzer(_FileAnalyzer):
    tool = "ruff"
    languages = _PYTHON
    # Порядок не важен: ни один префикс не является началом другого.
    families = ("C901", "N", "ANN", "D", "PLR")

    def _analyze_file(self, path: Path) -> AnalysisReport:
        command = [
            _ruff_binary(),
            "check",
            # Настройки проекта, в котором случайно окажется временный файл, не применяются.
            "--isolated",
            "--select",
            ",".join(self.families),
            # D203 и D213 несовместимы с D211 и D212; без явного выбора ruff пишет предупреждение.
            "--ignore",
            "D203,D213",
            "--output-format",
            "json",
            "--exit-zero",
            path.name,
        ]
        result = self._run(command, path.parent)
        raw = json.loads(result.stdout or "[]")
        metrics = dict.fromkeys(self.families, 0.0)
        findings: list[dict[str, Any]] = []
        for item in raw:
            code = item.get("code") or "syntax"
            family = next((f for f in self.families if code.startswith(f)), None)
            if family is not None:
                metrics[family] += 1
            findings.append(
                {"code": code, "message": item["message"], "line": item["location"]["row"]}
            )
        metrics["total"] = sum(metrics[family] for family in self.families)
        return AnalysisReport(tool=self.tool, metrics=metrics, findings=findings)


class RadonAnalyzer(_FileAnalyzer):
    tool = "radon"
    languages = _PYTHON

    def _radon(self, path: Path, *args: str) -> Any:
        command = [sys.executable, "-m", "radon", *args, "-j", path.name]
        result = self._run(command, path.parent)
        # radon cc не называет файл вовсе, если в нём нет ни функции, ни класса.
        return json.loads(result.stdout).get(path.name, [])

    def _analyze_file(self, path: Path) -> AnalysisReport:
        blocks = self._radon(path, "cc")
        if isinstance(blocks, dict) and "error" in blocks:
            return _note(self.tool, f"radon не разобрал код: {blocks['error']}")
        metrics: dict[str, float] = {}
        # Методы radon перечисляет и внутри класса, и отдельными блоками; класс не считается.
        complexities = [block["complexity"] for block in blocks if block["type"] != "class"]
        if complexities:
            metrics["cc_max"] = max(complexities)
            metrics["cc_mean"] = fmean(complexities)
        metrics["mi"] = self._radon(path, "mi")["mi"]
        halstead = self._radon(path, "hal")["total"]
        metrics["hal_volume"] = halstead["volume"]
        metrics["hal_difficulty"] = halstead["difficulty"]
        metrics["hal_bugs"] = halstead["bugs"]
        raw = self._radon(path, "raw")
        metrics["sloc"] = raw["sloc"]
        metrics["comment_ratio"] = (raw["comments"] + raw["multi"]) / max(raw["sloc"], 1)
        return AnalysisReport(tool=self.tool, metrics=metrics)


class AnnotationAnalyzer:
    """Доля аннотированных мест в сигнатурах. Считается по ast, без внешнего процесса."""

    tool = "annotations"
    languages = _PYTHON

    def analyze(self, code: str, language: str) -> AnalysisReport:
        try:
            tree = ast.parse(code)
        except SyntaxError as error:
            return _note(self.tool, f"код не разобран: {error.msg}, строка {error.lineno}")
        slots = annotated = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            arguments = node.args
            params = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
            params += [arg for arg in (arguments.vararg, arguments.kwarg) if arg is not None]
            # self и cls не аннотируют по соглашению, их отсутствие — не пропуск.
            if params and params[0].arg in {"self", "cls"}:
                params = params[1:]
            slots += len(params) + 1
            annotated += sum(param.annotation is not None for param in params)
            annotated += node.returns is not None
        metrics: dict[str, float] = {"slots": slots}
        if slots:
            metrics["ratio"] = annotated / slots
        return AnalysisReport(tool=self.tool, metrics=metrics)


class LizardAnalyzer(_FileAnalyzer):
    tool = "lizard"
    languages = _CPP

    def _analyze_file(self, path: Path) -> AnalysisReport:
        result = self._run([sys.executable, "-m", "lizard", "--csv", path.name], path.parent)
        # Колонки CSV lizard: NLOC, CCN, token, PARAM, length, location, file, function, ...
        rows = [row for row in csv.reader(io.StringIO(result.stdout)) if row]
        nloc = [int(row[0]) for row in rows]
        metrics: dict[str, float] = {"nloc": sum(nloc), "functions": len(rows)}
        if rows:
            ccn = [int(row[1]) for row in rows]
            metrics["ccn_max"] = max(ccn)
            metrics["ccn_mean"] = fmean(ccn)
            metrics["length_max"] = max(nloc)
            metrics["params_max"] = max(int(row[3]) for row in rows)
        findings = [{"function": row[7], "nloc": int(row[0]), "ccn": int(row[1])} for row in rows]
        return AnalysisReport(tool=self.tool, metrics=metrics, findings=findings)


_TIDY_WARNING = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):\d+: warning: (?P<msg>.*) \[(?P<check>[^\]]+)\]$"
)
_COGNITIVE = re.compile(r"cognitive complexity of (\d+)")
_TIDY_CHECKS = "-*,readability-function-cognitive-complexity,readability-identifier-naming"
# Порог 0 заставляет clang-tidy сообщить сложность каждой функции, а не только превышения.
# Имена функций не проверяются: в C++ равно приняты lower_case и camelBack.
_TIDY_CONFIG = json.dumps(
    {
        "CheckOptions": {
            "readability-function-cognitive-complexity.Threshold": 0,
            "readability-identifier-naming.ClassCase": "CamelCase",
            "readability-identifier-naming.StructCase": "CamelCase",
            "readability-identifier-naming.VariableCase": "lower_case",
            "readability-identifier-naming.ParameterCase": "lower_case",
        }
    }
)


class ClangTidyAnalyzer(_FileAnalyzer):
    tool = "clang_tidy"
    languages = _CPP

    def __init__(self, binary: str = "clang-tidy", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.binary = binary

    def _analyze_file(self, path: Path) -> AnalysisReport:
        command = [
            self.binary,
            path.name,
            f"--checks={_TIDY_CHECKS}",
            f"--config={_TIDY_CONFIG}",
            "--",
            "-std=c++17",
        ]
        result = self._run(command, path.parent)
        cognitive: list[int] = []
        naming = 0
        findings: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            match = _TIDY_WARNING.match(line)
            if match is None or Path(match["file"]).name != path.name:
                continue
            if match["check"] == "readability-function-cognitive-complexity":
                if value := _COGNITIVE.search(match["msg"]):
                    cognitive.append(int(value.group(1)))
            elif match["check"] == "readability-identifier-naming":
                naming += 1
                findings.append({"code": match["check"], "message": match["msg"]})
        if result.returncode != 0 and "error:" in result.stdout + result.stderr:
            findings.append({"note": "clang-tidy сообщил об ошибках компиляции"})
        metrics = {"cognitive_max": float(max(cognitive, default=0)), "naming": float(naming)}
        return AnalysisReport(tool=self.tool, metrics=metrics, findings=findings)


_CPPCHECK_COUNTED = {"error", "warning", "style", "performance", "portability"}


def parse_cppcheck_xml(xml: str) -> tuple[int, list[dict[str, Any]]]:
    """Замечания cppcheck без уровня information: там сообщения о ненайденных заголовках."""
    root = ElementTree.fromstring(xml)
    findings = [
        {"code": error.get("id"), "severity": error.get("severity"), "message": error.get("msg")}
        for error in root.iter("error")
        if error.get("severity") in _CPPCHECK_COUNTED
    ]
    return len(findings), findings


class CppcheckAnalyzer(_FileAnalyzer):
    tool = "cppcheck"
    languages = _CPP

    def __init__(self, binary: str = "cppcheck", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.binary = binary

    def _analyze_file(self, path: Path) -> AnalysisReport:
        command = [
            self.binary,
            "--enable=warning,style,performance,portability",
            "--xml",
            "--quiet",
            path.name,
        ]
        result = self._run(command, path.parent)
        # cppcheck пишет XML-отчёт в stderr.
        count, findings = parse_cppcheck_xml(result.stderr)
        return AnalysisReport(tool=self.tool, metrics={"issues": float(count)}, findings=findings)


def default_analyzers(timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> list[Analyzer]:
    return [
        RuffAnalyzer(timeout_seconds=timeout_seconds),
        RadonAnalyzer(timeout_seconds=timeout_seconds),
        AnnotationAnalyzer(),
        LizardAnalyzer(timeout_seconds=timeout_seconds),
        ClangTidyAnalyzer(timeout_seconds=timeout_seconds),
        CppcheckAnalyzer(timeout_seconds=timeout_seconds),
    ]


def run_analyzers(analyzers: list[Analyzer], code: str, language: str) -> list[AnalysisReport]:
    """Отчёты всех анализаторов, которые знают язык; неизвестный язык — пустой список."""
    name = canonical_language(language)
    return [analyzer.analyze(code, name) for analyzer in analyzers if name in analyzer.languages]
