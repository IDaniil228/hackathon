"""Командная строка для работы с БД.

    fspdb seed            справочники и банк шаблонов заданий
    fspdb seed-demo       демо-данные под сквозной сценарий
    fspdb relevance       пересчитать веса навыков из наблюдений
    fspdb dump-schema     бэкап DDL в .sql из моделей SQLAlchemy
    fspdb dump-data       бэкап содержимого таблиц в .sql
    fspdb stats           что сейчас в базе

Бэкап DDL собирается из тех же моделей, по которым Alembic строит миграции,
поэтому .sql-файл не может разойтись со схемой: он не пишется руками.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import decimal
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import create_mock_engine, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.schema import CreateIndex, CreateTable

from app.db.base import SCHEMAS, metadata
from app.db.enums import ALL_ENUMS
from app.db.models import assess, core, hire, matching, profile, ref  # noqa: F401
from app.db.relevance import recompute_skill_relevance
from app.db.seeds.demo import seed_demo
from app.db.seeds.reference import seed_reference
from app.db.seeds.task_bank import seed_task_bank
from app.db.session import build_engine

engine = build_engine()
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

#: Каталог бэкапов. В контейнере ./db смонтирован как /sql.
BACKUP_DIR = (
    Path("/sql/backup") if Path("/sql").is_dir() else Path(__file__).parents[3] / "db" / "backup"
)

EXTENSIONS = ("vector", "citext", "pg_trgm")


# --- наполнение --------------------------------------------------------------
async def cmd_seed() -> None:
    async with SessionFactory() as session:
        counts = await seed_reference(session)
        templates = await seed_task_bank(session)
        await session.commit()
    print(f"справочники: {counts}")
    print(f"шаблонов заданий: {templates}")


async def cmd_seed_demo(count=0, seed=20261011, as_of=None) -> None:
    async with SessionFactory() as session:
        if count:
            from app.db.seeds.synthetic import seed_synthetic

            counts = await seed_synthetic(session, count=count, seed=seed, as_of=as_of)
        else:
            counts = await seed_demo(session)
        await session.commit()
    print(f"демо-данные: {counts}")


async def cmd_relevance() -> None:
    async with SessionFactory() as session:
        rows = await recompute_skill_relevance(session)
        await session.commit()
    print(f"пересчитано весов: {rows}")


# --- бэкап DDL ---------------------------------------------------------------
def _enum_ddl() -> list[str]:
    out = []
    for e in ALL_ENUMS:
        labels = ", ".join(f"'{v}'" for v in e.enums)
        out.append(f"CREATE TYPE {e.schema}.{e.name} AS ENUM ({labels});")
    return out


def cmd_dump_schema() -> None:
    """Выгрузить DDL из метаданных SQLAlchemy — без обращения к живой базе."""
    chunks: list[str] = []

    def collect(sql, *_args, **_kw) -> None:
        statement = str(sql.compile(dialect=mock.dialect)).strip()
        if statement:
            chunks.append(statement.rstrip(";") + ";")

    mock = create_mock_engine("postgresql+psycopg://", collect)

    header = [
        "-- =============================================================================",
        "--  Платформа ФСП — снимок схемы базы данных",
        "-- =============================================================================",
        "--  Файл СГЕНЕРИРОВАН из моделей SQLAlchemy командой `fspdb dump-schema`.",
        "--  Править его руками бессмысленно: источник правды — app/db/models,",
        "--  а схему в базе разворачивает `alembic upgrade head`.",
        "--",
        "--  Это бэкап и материал для чтения: удобно открыть одним файлом, показать",
        "--  на защите и положить рядом с документацией.",
        f"--  Сгенерирован: {dt.datetime.now(dt.UTC).isoformat(timespec='seconds')}",
        "-- =============================================================================",
        "",
        "BEGIN;",
        "",
        "-- Схемы",
        *[f"CREATE SCHEMA IF NOT EXISTS {s};" for s in SCHEMAS],
        "",
        "-- Расширения",
        *[f"CREATE EXTENSION IF NOT EXISTS {e};" for e in EXTENSIONS],
        "",
        "-- ENUM-типы предметной области",
        *_enum_ddl(),
        "",
        "-- Таблицы и ограничения",
    ]

    for table in metadata.sorted_tables:
        chunks.append(
            str(CreateTable(table).compile(dialect=mock.dialect)).strip().rstrip(";") + ";"
        )

    index_ddl = ["", "-- Индексы"]
    for table in metadata.sorted_tables:
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            index_ddl.append(
                str(CreateIndex(index).compile(dialect=mock.dialect)).strip().rstrip(";") + ";"
            )

    body = "\n\n".join(chunks)
    text_out = "\n".join(header) + "\n\n" + body + "\n" + "\n".join(index_ddl) + "\n\nCOMMIT;\n"

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    out = BACKUP_DIR / "schema.sql"
    out.write_text(text_out, encoding="utf-8")
    print(
        f"{out}: {len(metadata.sorted_tables)} таблиц, {len(ALL_ENUMS)} типов, "
        f"{sum(len(t.indexes) for t in metadata.sorted_tables)} индексов"
    )


# --- бэкап данных ------------------------------------------------------------
def _literal(value: Any) -> str:
    """Представить значение SQL-литералом. Чистый Python, без pg_dump."""
    if value is None:
        return "NULL"
    # pgvector отдаёт вектор как numpy.ndarray: её str() обрезает середину
    # многоточием, поэтому разворачиваем в обычный список заранее
    if hasattr(value, "tolist") and not isinstance(value, (str, bytes, bytearray)):
        value = value.tolist()
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float, decimal.Decimal)):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return f"'{value.isoformat()}'"
    if isinstance(value, uuid.UUID):
        return f"'{value}'::uuid"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"'\\x{bytes(value).hex()}'::bytea"
    if isinstance(value, (dict,)):
        return "'" + json.dumps(value, ensure_ascii=False).replace("'", "''") + "'::jsonb"
    if isinstance(value, (list, tuple)):
        # вектор pgvector приходит списком чисел
        if value and all(isinstance(v, (int, float, decimal.Decimal)) for v in value):
            return "'[" + ",".join(f"{float(v):.6f}" for v in value) + "]'::vector"
        return "'" + json.dumps(list(value), ensure_ascii=False).replace("'", "''") + "'::jsonb"
    return "'" + str(value).replace("'", "''") + "'"


async def cmd_dump_data() -> None:
    lines = [
        "-- =============================================================================",
        "--  Платформа ФСП — снимок содержимого таблиц",
        "-- =============================================================================",
        "--  СГЕНЕРИРОВАН командой `fspdb dump-data` из текущей базы.",
        "--  Применять на схему, развёрнутую через `alembic upgrade head`.",
        f"--  Сгенерирован: {dt.datetime.now(dt.UTC).isoformat(timespec='seconds')}",
        "-- =============================================================================",
        "",
        "BEGIN;",
        "SET CONSTRAINTS ALL DEFERRED;",
        "",
    ]
    total = 0
    async with SessionFactory() as session:
        for table in metadata.sorted_tables:
            rows = (await session.execute(select(table))).mappings().all()
            if not rows:
                continue
            cols = list(rows[0].keys())
            # генерируемые колонки записывать нельзя
            writable = [c for c in cols if not getattr(table.c[c], "computed", None)]
            lines.append(f"-- {table.fullname}: {len(rows)}")
            col_list = ", ".join(f'"{c}"' for c in writable)
            # GENERATED ALWAYS AS IDENTITY не принимает явное значение без
            # OVERRIDING SYSTEM VALUE — а сохранить исходные id обязательно,
            # иначе разъедутся внешние ключи
            override = (
                " OVERRIDING SYSTEM VALUE"
                if any(table.c[c].identity is not None for c in writable)
                else ""
            )
            for row in rows:
                values = ", ".join(_literal(row[c]) for c in writable)
                lines.append(
                    f"INSERT INTO {table.fullname} ({col_list}){override} VALUES ({values});"
                )
            lines.append("")
            total += len(rows)

        # После ручных INSERT identity-последовательности остались на нуле:
        # следующий INSERT из приложения упал бы на дубликате ключа.
        # Все setval одним запросом, чтобы не засорять вывод psql.
        setvals = [
            f"setval(pg_get_serial_sequence('{table.fullname}', '{col.name}'), "
            f"coalesce((SELECT max({col.name}) FROM {table.fullname}), 1), true)"
            for table in metadata.sorted_tables
            for col in table.c
            if col.identity is not None
        ]
        if setvals:
            lines.append("-- Выравнивание identity-последовательностей")
            lines.append("SELECT " + ",\n       ".join(setvals) + ";")
    lines += ["", "COMMIT;", ""]

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    out = BACKUP_DIR / "data.sql"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"{out}: {total} строк")


# --- сводка ------------------------------------------------------------------
async def cmd_stats() -> None:
    async with SessionFactory() as session:
        # ":schemas::text[]" в text() не писать: двойное двоеточие сразу за
        # именем параметра сбивает разбор bind-параметров SQLAlchemy
        rows = await session.execute(
            text(
                "SELECT schemaname, count(*) FROM pg_tables WHERE schemaname = ANY(:schemas) GROUP BY 1"
            ),
            {"schemas": list(SCHEMAS)},
        )
        by_schema = dict(rows.all())
        print("таблиц по схемам:", {s: by_schema.get(s, 0) for s in SCHEMAS})
        print("всего таблиц:", sum(by_schema.values()))
        for label, model in [
            ("кандидатов", profile.Candidate),
            ("компаний", hire.Company),
            ("вакансий", hire.Vacancy),
            ("приглашений", hire.Invitation),
            ("категорий", ref.Category),
            ("навыков", ref.Skill),
            ("шаблонов заданий", assess.TaskTemplate),
            ("весов навыков", matching.SkillRelevance),
        ]:
            count = await session.scalar(select(text("count(*)")).select_from(model.__table__))
            print(f"{label}: {count}")


COMMANDS = {
    "seed": cmd_seed,
    "seed-demo": cmd_seed_demo,
    "relevance": cmd_relevance,
    "dump-schema": cmd_dump_schema,
    "dump-data": cmd_dump_data,
    "stats": cmd_stats,
}


def main() -> int:
    parser = argparse.ArgumentParser(prog="fspdb", description="Работа с БД платформы ФСП")
    parser.add_argument("command", choices=sorted(COMMANDS), help="что сделать")
    parser.add_argument(
        "--count", type=int, default=0, help="Число синтетических профилей для seed-demo"
    )
    parser.add_argument("--seed", type=int, default=20261011, help="Seed синтетического набора")
    parser.add_argument(
        "--as-of", type=dt.datetime.fromisoformat, help="Базовая дата с часовым поясом"
    )
    args = parser.parse_args()

    if args.count < 0 or args.count > 10000:
        parser.error("--count должен быть от 0 до 10000")
    if args.command == "seed-demo" and args.count:
        asyncio.run(_run(lambda: cmd_seed_demo(args.count, args.seed, args.as_of)))
        return 0

    handler = COMMANDS[args.command]
    if asyncio.iscoroutinefunction(handler):
        asyncio.run(_run(handler))
    else:
        handler()
    return 0


async def _run(handler) -> None:
    try:
        await handler()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
