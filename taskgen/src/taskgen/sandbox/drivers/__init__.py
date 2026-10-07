"""Драйверы: программы, которые исполняются в песочнице вокруг кода кандидата.

Драйвер языка — адаптер задач-функций к ядру песочницы: читает из stdin входы кейсов JSON,
вызывает функцию кандидата на каждом и печатает ответы JSON (протокол —
taskgen/sandbox/protocol.py). Ответы сверяет ядро, как вывод любой программы
(taskgen/sandbox/cases.py). Языку без драйвера доступны только задачи stdio.

Драйвер анализа (taskgen_analyze.py) запускает статические анализаторы на коде кандидата в
окружении analyzers. Исходники драйверов лежат рядом с этим модулем и исполняются только
внутри песочницы.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from importlib import resources

from taskgen.sandbox.errors import SandboxError

# В C++ имя функции попадает в исходный текст, поэтому допускается только идентификатор.
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _source(name: str) -> str:
    return resources.files(__package__).joinpath(name).read_text("utf-8")


Launch = tuple[dict[str, str], list[str]]


def _python(entrypoint: str, solution: str) -> Launch:
    files = {"taskgen_driver.py": _source("taskgen_driver.py"), "solution.py": solution}
    return files, [entrypoint]


def _cpp(entrypoint: str, solution: str) -> Launch:
    # Одна единица трансляции: скрипт compile пакета gcc добавляет .cpp к имени каждого
    # файла и собирает все, так что отдельный заголовок стал бы ещё одним .cpp.
    # Директивы #line сохраняют в сообщениях компилятора строки самого решения.
    source = (
        _source("prologue.hpp")
        + '#line 1 "solution.cpp"\n'
        + solution
        + f"\n#define TASKGEN_ENTRYPOINT {entrypoint}\n"
        + '#line 1 "taskgen_driver.cpp"\n'
        + _source("taskgen_driver.cpp")
    )
    return {"main.cpp": source}, []


# Драйверы задач-функций по имени: окружение называет свой в function_driver в runtimes.yaml.
DRIVERS: dict[str, Callable[[str, str], Launch]] = {"python": _python, "cpp": _cpp}


def driver_files(driver: str, entrypoint: str, solution: str) -> Launch:
    """Файлы запуска, первый из них исполняется, и аргументы запуска."""
    if not IDENTIFIER.match(entrypoint):
        raise SandboxError(f"entrypoint должен быть идентификатором, получено {entrypoint!r}")
    if driver not in DRIVERS:
        raise SandboxError(f"нет драйвера задач-функций {driver}")
    return DRIVERS[driver](entrypoint, solution)


ANALYZE_DRIVER = "taskgen_analyze.py"


def analysis_files(source_file: str, code: str) -> dict[str, str]:
    """Файлы запуска анализа: драйвер исполняется, код кандидата лежит рядом."""
    if source_file == ANALYZE_DRIVER:
        raise SandboxError(f"файл решения {source_file} совпал с драйвером анализа")
    return {ANALYZE_DRIVER: _source(ANALYZE_DRIVER), source_file: code}
