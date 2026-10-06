"""Командная строка сервиса. Подкоманды добавляют модули: импорт шаблонов — bank."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import uvicorn

from taskgen.bank.cli import import_command
from taskgen.config import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="taskgen", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="запустить HTTP-сервис")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)

    import_ = sub.add_parser(
        "import",
        help="загрузить папку md-шаблонов в банк",
        description="Проверяет все *.md папки и подпапок и пишет их одной транзакцией: "
        "при любой ошибке не пишется ничего, а все ошибки выводятся в stderr",
    )
    import_.add_argument("directory", type=Path, help="папка с шаблонами")
    import_.add_argument(
        "--no-embeddings", action="store_true", help="не считать эмбеддинги описаний после импорта"
    )

    args = parser.parse_args(argv)
    if args.command == "serve":
        uvicorn.run("taskgen.app:create_app", factory=True, host=args.host, port=args.port)
    if args.command == "import":
        return asyncio.run(
            import_command(args.directory, get_settings(), embeddings=not args.no_embeddings)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
