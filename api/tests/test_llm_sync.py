import importlib.util
from pathlib import Path


def test_generated_llm_modules_match_the_source():
    root = Path(__file__).resolve().parents[1]
    specification = importlib.util.spec_from_file_location(
        "api_llm_generator", root / "scripts/sync_llm.py"
    )
    generator = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(generator)
    expected = generator.render()
    actual = {
        path.relative_to(root / "app/llm").as_posix(): path.read_text(encoding="utf-8")
        for path in (root / "app/llm").rglob("*.py")
    }
    assert actual == expected
