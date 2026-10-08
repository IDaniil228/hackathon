"""Окружение Alembic.

URL подключения берётся из настроек приложения, а не из alembic.ini: источник
правды один — переменная окружения DATABASE_URL.

``include_schemas=True`` обязателен: таблицы разложены по шести схемам, без
этого флага автогенерация их не увидит и предложит всё удалить.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db.base import SCHEMAS, metadata
from app.db.models import *  # noqa: F401,F403  — регистрирует таблицы в metadata
from app.settings import get_settings

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().sync_database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = metadata

#: Служебные объекты, которые не описаны моделями и которые автогенерация
#: не должна предлагать удалить.
IGNORED_TABLES = {"alembic_version"}


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table":
        if name in IGNORED_TABLES:
            return False
        # Чужие схемы (public с расширениями) нас не касаются
        return obj.schema in SCHEMAS
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_object=include_object,
        compare_type=True,
        compare_server_default=True,
        version_table_schema=None,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_object=include_object,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
