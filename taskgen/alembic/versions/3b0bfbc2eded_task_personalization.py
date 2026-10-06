"""Что из профиля кандидата повлияло на легенду задания

Revision ID: 3b0bfbc2eded
Revises: bd1893e570a6
Create Date: 2026-10-06 20:57:30.409313
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3b0bfbc2eded"
down_revision: str | Sequence[str] | None = "bd1893e570a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("personalization", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="taskgen",
    )


def downgrade() -> None:
    op.drop_column("tasks", "personalization", schema="taskgen")
