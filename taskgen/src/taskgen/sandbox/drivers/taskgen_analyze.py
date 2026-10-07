"""Статический анализ кода кандидата. Исполняется внутри песочницы, а не в сервисе.

Драйвер запускается в окружении analyzers (taskgen/sandbox/packages/analyzers) рядом с
файлом решения. Аргументы: язык (python или cpp), имя файла решения и бюджет времени на все
инструменты в секундах. Инструменты читают файл и не исполняют его. В stdout уходит один
JSON-список отчётов {"tool", "metrics", "findings"}; ключи метрик описаны в рубриках
taskgen/src/taskgen/evaluation/rubrics. Инструмента нет, он упал или не уложился в бюджет —
отчёт пустой, с пометкой в findings.

Код кандидата здесь только разбирается, поэтому весь stdout принадлежит драйверу: вывод
инструментов он перехватывает и в stdout не пропускает.
"""

import ast
import csv
import io
import json
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from statistics import fmean

RUFF_FAMILIES = ("C901", "N", "ANN", "D", "PLR")
TIDY_WARNING = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):\d+: warning: (?P<msg>.*) \[(?P<check>[^\]]+)\]$"
)
COGNITIVE = re.compile(r"cognitive complexity of (\d+)")
TIDY_CHECKS = "-*,readability-function-cognitive-complexity,readability-identifier-naming"
# Порог 0 заставляет clang-tidy сообщить сложность каждой функции, а не только превышения.
# Имена функций не проверяются: в C++ равно приняты lower_case и camelBack.
TIDY_CONFIG = json.dumps(
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
CPPCHECK_COUNTED = {"error", "warning", "style", "performance", "portability"}


class ToolMissingError(Exception):
    pass


class ToolTimeoutError(Exception):
    pass


def binary(name: str) -> str:
    """Путь к инструменту из колеса Python, иначе имя для поиска в PATH.

    Скрипты, которые pip кладёт в bin/, ссылаются в первой строке на интерпретатор каталога
    сборки пакета, а не на каталог, куда пакет установлен в Piston. Поэтому бинарник берётся
    прямо из колеса.
    """
    try:
        if name == "clang-tidy":
            import clang_tidy

            return str(clang_tidy.get_executable(name))
        if name == "cppcheck":
            import cppcheck

            return str(cppcheck.get_cppcheck_dir() / name)
    except (ImportError, FileNotFoundError):
        pass
    return name


class Tools:
    """Запуск инструментов в общем бюджете времени."""

    def __init__(self, budget: float, cwd: Path) -> None:
        self.budget = budget
        self.cwd = cwd
        self.deadline = time.monotonic() + budget

    def run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        if shutil.which(command[0]) is None:
            raise ToolMissingError(command[0])
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ToolTimeoutError
        try:
            return subprocess.run(
                command,
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=remaining,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise ToolTimeoutError from error

    def module(self, name: str, *args: str) -> subprocess.CompletedProcess[str]:
        return self.run([sys.executable, "-m", name, *args])


def note(tool: str, text: str) -> dict:
    return {"tool": tool, "metrics": {}, "findings": [{"note": text}]}


def report(tool: str, metrics: dict, findings: list | None = None) -> dict:
    return {"tool": tool, "metrics": metrics, "findings": findings or []}


def guarded(tool: str, analyze, tools: Tools, path: Path) -> dict:
    try:
        return analyze(tools, path)
    except ToolMissingError as error:
        return note(tool, f"инструмент {error} не найден")
    except ToolTimeoutError:
        return note(tool, f"анализ не уложился в {tools.budget:g} с")
    except (RecursionError, MemoryError):
        return note(tool, "код слишком глубоко вложен для разбора")
    except Exception as error:  # noqa: BLE001 — отказ одного инструмента не губит остальные
        return note(tool, f"инструмент упал: {type(error).__name__}")


# --- Python ------------------------------------------------------------------------------


def ruff(tools: Tools, path: Path) -> dict:
    result = tools.module(
        "ruff",
        "check",
        # Настройки проекта, в котором случайно окажется файл, не применяются.
        "--isolated",
        "--select",
        ",".join(RUFF_FAMILIES),
        # D203 и D213 несовместимы с D211 и D212; без явного выбора ruff пишет предупреждение.
        "--ignore",
        "D203,D213",
        "--output-format",
        "json",
        "--exit-zero",
        path.name,
    )
    metrics = dict.fromkeys(RUFF_FAMILIES, 0.0)
    findings = []
    for item in json.loads(result.stdout or "[]"):
        code = item.get("code") or "syntax"
        family = next((f for f in RUFF_FAMILIES if code.startswith(f)), None)
        if family is not None:
            metrics[family] += 1
        findings.append({"code": code, "message": item["message"], "line": item["location"]["row"]})
    metrics["total"] = sum(metrics[family] for family in RUFF_FAMILIES)
    return report("ruff", metrics, findings)


def radon(tools: Tools, path: Path) -> dict:
    def run(*args: str):
        result = tools.module("radon", *args, "-j", path.name)
        # radon cc не называет файл вовсе, если в нём нет ни функции, ни класса.
        return json.loads(result.stdout).get(path.name, [])

    blocks = run("cc")
    if isinstance(blocks, dict) and "error" in blocks:
        return note("radon", f"radon не разобрал код: {blocks['error']}")
    metrics: dict[str, float] = {}
    # Методы radon перечисляет и внутри класса, и отдельными блоками; класс не считается.
    complexities = [block["complexity"] for block in blocks if block["type"] != "class"]
    if complexities:
        metrics["cc_max"] = max(complexities)
        metrics["cc_mean"] = fmean(complexities)
    metrics["mi"] = run("mi")["mi"]
    halstead = run("hal")["total"]
    metrics["hal_volume"] = halstead["volume"]
    metrics["hal_difficulty"] = halstead["difficulty"]
    metrics["hal_bugs"] = halstead["bugs"]
    raw = run("raw")
    metrics["sloc"] = raw["sloc"]
    metrics["comment_ratio"] = (raw["comments"] + raw["multi"]) / max(raw["sloc"], 1)
    return report("radon", metrics)


def annotations(tools: Tools, path: Path) -> dict:
    """Доля аннотированных мест в сигнатурах функций."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as error:
        return note("annotations", f"код не разобран: {error.msg}, строка {error.lineno}")
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
    return report("annotations", metrics)


# --- C++ ---------------------------------------------------------------------------------


def lizard(tools: Tools, path: Path) -> dict:
    result = tools.module("lizard", "--csv", path.name)
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
    return report("lizard", metrics, findings)


def clang_tidy(tools: Tools, path: Path) -> dict:
    result = tools.run(
        [
            binary("clang-tidy"),
            path.name,
            f"--checks={TIDY_CHECKS}",
            f"--config={TIDY_CONFIG}",
            "--",
            "-std=c++17",
        ]
    )
    cognitive: list[int] = []
    naming = 0
    findings: list[dict] = []
    for line in result.stdout.splitlines():
        match = TIDY_WARNING.match(line)
        if match is None or Path(match["file"]).name != path.name:
            continue
        if match["check"] == "readability-function-cognitive-complexity":
            if value := COGNITIVE.search(match["msg"]):
                cognitive.append(int(value.group(1)))
        elif match["check"] == "readability-identifier-naming":
            naming += 1
            findings.append({"code": match["check"], "message": match["msg"]})
    if result.returncode != 0 and "error:" in result.stdout + result.stderr:
        findings.append({"note": "clang-tidy сообщил об ошибках компиляции"})
    metrics = {"cognitive_max": float(max(cognitive, default=0)), "naming": float(naming)}
    return report("clang_tidy", metrics, findings)


def cppcheck(tools: Tools, path: Path) -> dict:
    result = tools.run(
        [
            binary("cppcheck"),
            "--enable=warning,style,performance,portability",
            "--xml",
            "--quiet",
            path.name,
        ]
    )
    # cppcheck пишет XML-отчёт в stderr. Уровень information не считается: там сообщения о
    # ненайденных заголовках.
    root = ElementTree.fromstring(result.stderr)
    findings = [
        {"code": error.get("id"), "severity": error.get("severity"), "message": error.get("msg")}
        for error in root.iter("error")
        if error.get("severity") in CPPCHECK_COUNTED
    ]
    return report("cppcheck", {"issues": float(len(findings))}, findings)


ANALYZERS = {
    "python": [("ruff", ruff), ("radon", radon), ("annotations", annotations)],
    "cpp": [("lizard", lizard), ("clang_tidy", clang_tidy), ("cppcheck", cppcheck)],
}


def main() -> None:
    language, source, budget = sys.argv[1], sys.argv[2], float(sys.argv[3])
    path = Path(source).resolve()
    tools = Tools(budget, path.parent)
    reports = [guarded(tool, analyze, tools, path) for tool, analyze in ANALYZERS[language]]
    sys.stdout.write(json.dumps(reports, ensure_ascii=False))


if __name__ == "__main__":
    main()
