"""Базовый класс моделей и общие соглашения по именованию объектов БД."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Схемы в порядке зависимостей. Их создаёт первая миграция.
SCHEMAS: tuple[str, ...] = ("core", "ref", "profile", "assess", "hire", "match")

# Имена ограничений и индексов задаются шаблоном, а не на усмотрение БД.
# Без этого у автоматически созданных объектов имена разъезжаются между
# окружениями, и Alembic начинает видеть различия там, где их нет.
NAMING_CONVENTION = {
    "ix": "%(table_name)s_%(column_0_N_name)s_idx",
    "uq": "%(table_name)s_%(column_0_N_name)s_key",
    "ck": "%(table_name)s_%(constraint_name)s",
    "fk": "%(table_name)s_%(column_0_N_name)s_fkey",
    "pk": "%(table_name)s_pkey",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    metadata = metadata
