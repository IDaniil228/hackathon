"""Объяснение отбора шаблонов в test_sessions.selection

Revision ID: 9a576e5eb2eb
Revises: cc993ce82ae6
Create Date: 2026-10-06 19:33:11.585526
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9a576e5eb2eb"
down_revision: str | Sequence[str] | None = "cc993ce82ae6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "test_sessions",
        sa.Column("selection", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="taskgen",
    )


def downgrade() -> None:
    op.drop_column("test_sessions", "selection", schema="taskgen")
