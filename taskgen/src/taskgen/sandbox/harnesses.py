"""Сборка файлов запуска из решения кандидата и обвязки тестов нужного языка.

Исходники обвязок лежат в каталоге harness/ и исполняются только внутри песочницы.
"""

from __future__ import annotations

import re
from importlib import resources

from taskgen.sandbox.errors import SandboxError

# В C++ имя функции попадает в исходный текст, поэтому допускается только идентификатор.
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _source(name: str) -> str:
    return resources.files(__package__).joinpath("harness", name).read_text("utf-8")


def harness_files(
    kind: str, entrypoint: str, solution: str
) -> tuple[list[dict[str, str]], list[str]]:
    """Возвращает файлы для Piston, первый из них запускается, и аргументы запуска."""
    if not IDENTIFIER.match(entrypoint):
        raise SandboxError(f"entrypoint должен быть идентификатором, получено {entrypoint!r}")
    if kind == "python":
        files = [
            {"name": "taskgen_harness.py", "content": _source("taskgen_harness.py")},
            {"name": "solution.py", "content": solution},
        ]
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
            + '#line 1 "taskgen_harness.cpp"\n'
            + _source("taskgen_harness.cpp")
        )
        return [{"name": "main.cpp", "content": source}], []
    raise SandboxError(f"нет обвязки тестов для языка {kind}")
