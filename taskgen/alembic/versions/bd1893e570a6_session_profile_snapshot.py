"""Снимок профиля кандидата в сессии, его хэш и отметка об изменении профиля

Revision ID: bd1893e570a6
Revises: 1839d67415e0
Create Date: 2026-10-06 20:53:55.889591
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "bd1893e570a6"
down_revision: str | Sequence[str] | None = "1839d67415e0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "test_sessions",
        sa.Column("profile", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="taskgen",
    )
    op.add_column(
        "test_sessions",
        sa.Column("profile_hash", sa.String(length=64), nullable=True),
        schema="taskgen",
    )
    op.add_column(
        "test_sessions",
        sa.Column("profile_changed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema="taskgen",
    )


def downgrade() -> None:
    op.drop_column("test_sessions", "profile_changed", schema="taskgen")
    op.drop_column("test_sessions", "profile_hash", schema="taskgen")
    op.drop_column("test_sessions", "profile", schema="taskgen")
