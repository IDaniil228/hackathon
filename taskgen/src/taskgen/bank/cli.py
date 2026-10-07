"""Подкоманда taskgen import: загрузка папки md-шаблонов в БД."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TextIO

from taskgen.bank.embeddings import refresh_embeddings_safely
from taskgen.bank.importer import import_documents, read_directory
from taskgen.bank.parser import TemplateError
from taskgen.bank.validation import TemplateValidator
from taskgen.config import Settings
from taskgen.contracts import Embedder, SandboxRunner
from taskgen.db import make_sessionmaker
from taskgen.llm import ProviderRouter
from taskgen.sandbox.errors import SandboxServiceError
from taskgen.sandbox.piston import PistonRunner

EXIT_INVALID = 1
EXIT_USAGE = 2
EXIT_SANDBOX = 3


async def import_command(directory: Path, settings: Settings, *, embeddings: bool) -> int:
    """Точка входа из taskgen.cli: эмбеддер — провайдер роли embedding, песочница — Piston."""
    # Без настройки логирования ошибка эмбеддера из refresh_embeddings_safely не видна.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    client = (
        ProviderRouter.from_settings(settings.llm, roles=("embedding",)) if embeddings else None
    )
    sandbox = PistonRunner(settings.sandbox)
    try:
        return await run_import(
            directory, settings, embedder=client, sandbox=sandbox, out=sys.stdout, err=sys.stderr
        )
    finally:
        await sandbox.aclose()
        if client is not None:
            await client.aclose()


async def run_import(
    directory: Path,
    settings: Settings,
    *,
    embedder: Embedder | None,
    sandbox: SandboxRunner,
    out: TextIO,
    err: TextIO,
) -> int:
    """Проверяет все *.md папки, пишет их одной транзакцией и досчитывает эмбеддинги.

    Код возврата: 0 — успех, 1 — шаблоны не прошли проверку или их нет, 2 — нет папки,
    3 — песочница недоступна, и шаблоны с кодом не проверить.
    """
    if not directory.is_dir():
        print(f"taskgen import: {directory} не папка", file=err)
        return EXIT_USAGE
    docs = read_directory(directory)
    if not docs:
        print(f"taskgen import: в {directory} нет файлов *.md", file=err)
        return EXIT_INVALID

    check = TemplateValidator.from_settings(sandbox, settings)
    sessionmaker = make_sessionmaker(settings.database_url)
    try:
        try:
            async with sessionmaker() as session, session.begin():
                outcomes = await import_documents(session, docs, check=check)
        except TemplateError as error:
            for issue in error.issues:
                print(issue, file=err)
            files = len({issue.source for issue in error.issues})
            print(
                f"Ничего не записано: {len(error.issues)} ошибок в {files} файлах из {len(docs)}",
                file=err,
            )
            return EXIT_INVALID
        except SandboxServiceError as error:
            print(
                f"Ничего не записано: шаблоны с кодом не проверены, песочница не ответила: {error}",
                file=err,
            )
            return EXIT_SANDBOX

        for outcome in outcomes:
            print(
                f"{outcome.status.value:9} {outcome.name} v{outcome.version}  ({outcome.source})",
                file=out,
            )
        if embedder is not None:
            written = await refresh_embeddings_safely(sessionmaker, embedder)
            print(f"Эмбеддингов посчитано: {written} (модель {embedder.model_name})", file=out)
        return 0
    finally:
        await sessionmaker.kw["bind"].dispose()
