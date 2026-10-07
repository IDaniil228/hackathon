"""Драйверы языков: адаптер задач-функций к ядру песочницы.

Драйвер — программа, которая читает из stdin входы кейсов JSON, вызывает функцию кандидата
на каждом и печатает ответы JSON (протокол — taskgen/sandbox/protocol.py). Ответы сверяет
ядро, как вывод любой программы (taskgen/sandbox/cases.py). Исходники драйверов лежат рядом
с этим модулем и исполняются только внутри песочницы. Языку без драйвера доступны только
задачи stdio.
"""

from __future__ import annotations

import re
from importlib import resources

from taskgen.sandbox.errors import SandboxError

# В C++ имя функции попадает в исходный текст, поэтому допускается только идентификатор.
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _source(name: str) -> str:
    return resources.files(__package__).joinpath(name).read_text("utf-8")


def driver_files(kind: str, entrypoint: str, solution: str) -> tuple[dict[str, str], list[str]]:
    """Файлы запуска, первый из них исполняется, и аргументы запуска."""
    if not IDENTIFIER.match(entrypoint):
        raise SandboxError(f"entrypoint должен быть идентификатором, получено {entrypoint!r}")
    if kind == "python":
        files = {"taskgen_driver.py": _source("taskgen_driver.py"), "solution.py": solution}
        return files, [entrypoint]
    if kind == "cpp":
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
    raise SandboxError(f"нет драйвера задач-функций для языка {kind}")
