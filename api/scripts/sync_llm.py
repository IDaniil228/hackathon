import argparse
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "api/app/llm"


def render():
    specification = importlib.util.spec_from_file_location(
        "shared_llm_generator", ROOT / "matcher/scripts/sync_llm.py"
    )
    generator = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(generator)
    return generator.render(package="app.llm", script="api/scripts/sync_llm.py")


def main():
    parser = argparse.ArgumentParser(
        description="Обновить производную копию общего LLM-слоя taskgen для API"
    )
    parser.add_argument("--out", type=Path, default=TARGET, help="Каталог производных модулей")
    args = parser.parse_args()
    for relative, text in render().items():
        path = args.out / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(relative)


if __name__ == "__main__":
    main()
