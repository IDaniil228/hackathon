"""Модели всех шести схем.

Импорт пакета регистрирует таблицы в общей ``MetaData`` — на это опирается и
Alembic, и выгрузка DDL.
"""

from app.db.base import SCHEMAS, Base, metadata
from app.db.models import assess, backend, core, hire, matching, messaging, profile, ref

__all__ = [
    "Base",
    "metadata",
    "SCHEMAS",
    "core",
    "ref",
    "profile",
    "assess",
    "hire",
    "matching",
    "backend",
    "messaging",
]
