"""Ранний старт сессии: кандидат начинает, когда готово первое задание, а не все

Сессии, созданные до миграции, начинались именно так, поэтому они получают true. Новые
сессии по умолчанию закрыты, пока готовы не все задания.

Revision ID: 7c4e2a91d5f3
Revises: 5d0c3a7e9b21
Create Date: 2026-10-07 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c4e2a91d5f3"
down_revision: str | Sequence[str] | None = "5d0c3a7e9b21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "test_sessions",
        sa.Column("early_start", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        schema="taskgen",
    )
    op.alter_column(
        "test_sessions", "early_start", server_default=sa.text("false"), schema="taskgen"
    )


def downgrade() -> None:
    op.drop_column("test_sessions", "early_start", schema="taskgen")
