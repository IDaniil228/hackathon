"""Командная строка сервиса: HTTP-сервис и пересчёт векторов карточек."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

import uvicorn

from matcher.config import Settings, get_settings
from matcher.db import make_engine, make_sessionmaker
from matcher.embeddings import check_dimension
from matcher.llm.contracts import Embedder
from matcher.llm.llm import ProviderRouter
from matcher.runs import RunService
from matcher.summaries import SummarySource, TaskgenSummaries


async def embed_command(
    settings: Settings,
    *,
    embedder: Embedder | None = None,
    summaries: SummarySource | None = None,
) -> int:
    """Досчитывает векторы всех кандидатов, которых можно найти; 0 — успех."""
    own_router = None
    if embedder is None:
        own_router = ProviderRouter.from_settings(settings.llm, roles={"embedding"})
        embedder = own_router
    own_summaries = None
    if summaries is None and settings.taskgen.base_url:
        own_summaries = summaries = TaskgenSummaries(settings.taskgen)
    engine = make_engine(settings.database_url)
    sessionmaker = make_sessionmaker(engine)
    service = RunService(sessionmaker, settings, embedder=embedder, summaries=summaries)
    try:
        async with sessionmaker() as session:
            await check_dimension(session, embedder)
        total, written = await service.refresh_embeddings()
    finally:
        if own_summaries is not None:
            await own_summaries.aclose()
        if own_router is not None:
            await own_router.aclose()
        await engine.dispose()
    print(f"кандидатов {total}, векторов записано {written}, модель {embedder.model_name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="matcher", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="запустить HTTP-сервис")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8002)

    sub.add_parser(
        "embed",
        help="досчитать векторы карточек кандидатов",
        description="Считает векторы карточек всех кандидатов, которых можно найти, и "
        "пропускает карточки, текст которых не менялся с прошлого пересчёта",
    )

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    if args.command == "serve":
        uvicorn.run("matcher.app:create_app", factory=True, host=args.host, port=args.port)
    if args.command == "embed":
        return asyncio.run(embed_command(get_settings()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
