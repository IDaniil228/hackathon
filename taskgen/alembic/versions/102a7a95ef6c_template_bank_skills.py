"""Навыки банка шаблонов, которых не было в начальном справочнике

Revision ID: 102a7a95ef6c
Revises: 9a576e5eb2eb
Create Date: 2026-10-06 19:47:13.237651
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "102a7a95ef6c"
down_revision: str | Sequence[str] | None = "9a576e5eb2eb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SKILLS = {
    "binary-search": ("Бинарный поиск", ["algo"]),
    "data-structures": ("Структуры данных", ["algo"]),
    "ml-metrics": ("Метрики качества моделей", ["ml"]),
    "data-preprocessing": ("Предобработка данных", ["ml"]),
    "optimization": ("Градиентная оптимизация", ["ml"]),
    "clustering": ("Кластеризация", ["ml"]),
    "http-api": ("HTTP API", ["backend"]),
    "caching": ("Кэширование", ["backend"]),
    "security": ("Безопасность", ["backend"]),
    "observability": ("Логи и метрики", ["backend"]),
}


def _tables() -> tuple[sa.Table, sa.Table]:
    meta = sa.MetaData(schema="taskgen")
    skills = sa.Table("skills", meta, autoload_with=op.get_bind())
    area_skills = sa.Table("area_skills", meta, autoload_with=op.get_bind())
    return skills, area_skills


def upgrade() -> None:
    skills, area_skills = _tables()
    op.bulk_insert(skills, [{"code": c, "title": t} for c, (t, _) in SKILLS.items()])
    op.bulk_insert(
        area_skills,
        [{"area_code": a, "skill_code": c} for c, (_, ar) in SKILLS.items() for a in ar],
    )


def downgrade() -> None:
    skills, area_skills = _tables()
    codes = list(SKILLS)
    op.execute(area_skills.delete().where(area_skills.c.skill_code.in_(codes)))
    op.execute(skills.delete().where(skills.c.code.in_(codes)))
