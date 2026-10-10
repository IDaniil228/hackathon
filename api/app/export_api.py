import argparse
import json
from pathlib import Path

from app.main import create_app


def main():
    parser = argparse.ArgumentParser(
        description="Экспортировать OpenAPI из приложения без запуска БД"
    )
    parser.add_argument("--output", type=Path, required=True, help="Путь JSON-файла спецификации")
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(args.output)


if __name__ == "__main__":
    main()
