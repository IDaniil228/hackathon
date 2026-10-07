"""Навыки шаблонов stdio: Bash и разбор текстовых форматов

Revision ID: 5d0c3a7e9b21
Revises: fa1ff5d486f4
Create Date: 2026-10-07 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5d0c3a7e9b21"
down_revision: str | Sequence[str] | None = "fa1ff5d486f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SKILLS = {
    "bash": ("Bash", ["backend"]),
    "text-processing": ("Разбор текстовых форматов", ["backend"]),
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
