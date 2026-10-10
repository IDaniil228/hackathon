"""Копия LLM-слоя taskgen совпадает с источником: scripts/sync_llm.py."""

import importlib.util
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
COPY = PROJECT / "src" / "matcher" / "llm"
SOURCE = PROJECT.parent / "taskgen" / "src" / "taskgen"


def _sync_module():
    spec = importlib.util.spec_from_file_location("sync_llm", PROJECT / "scripts" / "sync_llm.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["sync_llm"] = module
    spec.loader.exec_module(module)
    return module


def _tree(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*.py")}


def test_committed_copy_matches_fresh_sync(tmp_path: Path) -> None:
    _sync_module().main(["--out", str(tmp_path)])
    fresh, committed = _tree(tmp_path), _tree(COPY)
    assert sorted(fresh) == sorted(committed), "состав копии разошёлся: scripts/sync_llm.py"
    stale = [name for name in fresh if fresh[name] != committed[name]]
    assert not stale, f"копия устарела, запустите scripts/sync_llm.py: {stale}"


def test_copy_differs_from_source_only_in_header_and_imports() -> None:
    copied = [name for name in _tree(COPY) if not name.endswith("__init__.py")]
    assert "llm.py" in copied and "evaluation/jsonout.py" in copied
    for name in copied:
        header, *body = (COPY / name).read_text(encoding="utf-8").splitlines()
        source = (SOURCE / name).read_text(encoding="utf-8").splitlines()
        assert f"taskgen/src/taskgen/{name}" in header
        assert len(body) == len(source), name
        for ours, theirs in zip(body, source, strict=True):
            if ours == theirs:
                continue
            assert theirs.lstrip().startswith(("from taskgen", "import taskgen")), (name, theirs)
            assert ours == theirs.replace("taskgen", "matcher.llm", 1), (name, ours)
