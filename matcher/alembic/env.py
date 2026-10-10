"""Окружение alembic матчера: только схема matcher, адрес БД — из настроек сервиса.

В той же базе живут таблицы api, у них свои миграции в api/migrations. Фильтр include_name
скрывает их от autogenerate, иначе он предложил бы удалить всю схему api. Схему matcher
создаёт db-init.sql: у роли matcher нет права CREATE на базу.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from matcher.config import get_settings
from matcher.db import CONNECT_ARGS, SCHEMA, Base

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_name(name: str | None, type_: str, parent_names: dict) -> bool:
    if type_ == "schema":
        return name == SCHEMA
    # Таблица версий лежит в схеме сервиса, но моделей у неё нет.
    return not (type_ == "table" and name == "alembic_version")


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        include_schemas=True,
        include_name=include_name,
        version_table_schema=SCHEMA,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(url=config.get_main_option("sqlalchemy.url"), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=CONNECT_ARGS,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
