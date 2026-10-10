"""Копия LLM-слоя taskgen в src/matcher/llm.

    uv run python scripts/sync_llm.py            переписывает src/matcher/llm/
    uv run python scripts/sync_llm.py --out DIR  пишет копию в DIR

Корни копии перечислены в ROOTS. Скрипт добавляет к ним модули taskgen, которые они
импортируют, на любой глубине. Модуль копируется байт в байт, кроме двух правок: в начало
встаёт строка о происхождении файла, а в строках импорта taskgen заменяется на matcher.llm.
Пакеты получают свой __init__.py со строкой о происхождении. tests/test_llm_sync.py
собирает копию заново и сравнивает её с закоммиченной.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT.parent
SOURCE = ROOT / "taskgen" / "src" / "taskgen"
TARGET = PROJECT / "src" / "matcher" / "llm"
SCRIPT = "matcher/scripts/sync_llm.py"

# Что нужно матчеру: клиент и маршрут по ролям, общая часть настроек LLM, контракты, маркеры,
# ответ по схеме с запасным разбором JSON, доказательство вердикта и проверка текста по
# фактам. Настройки самого taskgen (config.py) и machine summary сюда не входят.
ROOTS = (
    "llm.py",
    "llm_config.py",
    "contracts.py",
    "markers.py",
    "evaluation/jsonout.py",
    "evaluation/evidence.py",
    "evaluation/factcheck.py",
)

_IMPORT = re.compile(r"^(\s*(?:from|import)\s+)taskgen(?=[.\s])", re.MULTILINE)
_IMPORTED = re.compile(r"^\s*(?:from|import)\s+taskgen((?:\.\w+)*)(?:\s+import\s+(.+))?", re.M)


def _header(relative: str) -> str:
    return f"# Копия taskgen/src/taskgen/{relative} от {SCRIPT}, не править\n"


def _dependencies(relative: str, text: str) -> set[str]:
    """Модули taskgen, которые импортирует text, путями от SOURCE."""
    found = set()
    for module, names in _IMPORTED.findall(text):
        parts = [part for part in module.split(".") if part]
        candidates = [parts]
        # from taskgen.pkg import name: name бывает модулем пакета.
        if names:
            for name in re.findall(r"\w+", names.split("#")[0]):
                candidates.append([*parts, name])
        for path in candidates:
            if path and (SOURCE / Path(*path).with_suffix(".py")).is_file():
                found.add("/".join(path) + ".py")
    found.discard(relative)
    return found


def closure(roots: tuple[str, ...] = ROOTS) -> list[str]:
    """Корни и все модули taskgen, до которых они доходят импортами."""
    seen: set[str] = set()
    pending = list(roots)
    while pending:
        relative = pending.pop()
        if relative in seen:
            continue
        seen.add(relative)
        pending.extend(_dependencies(relative, (SOURCE / relative).read_text(encoding="utf-8")))
    return sorted(seen)


def render() -> dict[str, str]:
    """Содержимое копии: путь от TARGET → текст файла."""
    files: dict[str, str] = {}
    for relative in closure():
        text = (SOURCE / relative).read_text(encoding="utf-8")
        files[relative] = _header(relative) + _IMPORT.sub(r"\1matcher.llm", text)
    packages = {str(Path(relative).parent) for relative in files} | {"."}
    for package in packages:
        init = "__init__.py" if package == "." else f"{package}/__init__.py"
        files[init] = (
            f"# Создан {SCRIPT}, не править\n"
            '"""Копия части LLM-слоя taskgen; источник правды — taskgen/src/taskgen."""\n'
        )
    return dict(sorted(files.items()))


def write(out: Path) -> list[str]:
    """Пишет копию в out и удаляет там *.py, которых в копии нет. Возвращает пути."""
    files = render()
    for relative, text in files.items():
        path = out / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for stale in out.rglob("*.py"):
        if stale.relative_to(out).as_posix() not in files:
            stale.unlink()
    return list(files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=TARGET, help="куда писать копию")
    args = parser.parse_args(argv)
    for relative in write(args.out):
        print(relative)
    return 0


if __name__ == "__main__":
    sys.exit(main())
