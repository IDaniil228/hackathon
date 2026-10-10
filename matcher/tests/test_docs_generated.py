"""Закоммиченная документация docs/matcher/generated совпадает с тем, что собирает генератор."""

import difflib
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "gen_docs.py"
COMMAND = "cd matcher && uv run python scripts/gen_docs.py"


def _generator():
    spec = importlib.util.spec_from_file_location("gen_docs", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_docs_are_current(tmp_path: Path):
    gen = _generator()
    fresh = {path.name: path for path in gen.write(tmp_path)}
    committed = (
        {path.name: path for path in gen.GENERATED.iterdir() if path.is_file()}
        if gen.GENERATED.is_dir()
        else {}
    )

    stale = []
    for name in sorted(fresh.keys() | committed.keys()):
        if name not in committed or name not in fresh:
            stale.append(
                f"{name}: файл есть только в {'новой' if name in fresh else 'старой'} сборке"
            )
            continue
        new = fresh[name].read_text(encoding="utf-8").splitlines()
        old = committed[name].read_text(encoding="utf-8").splitlines()
        if new != old:
            diff = difflib.unified_diff(
                old, new, f"закоммичено/{name}", f"собрано/{name}", n=0, lineterm=""
            )
            stale.append("\n".join(list(diff)[:12]))
    assert not stale, f"Документация устарела, пересоберите её: {COMMAND}\n\n" + "\n\n".join(stale)
