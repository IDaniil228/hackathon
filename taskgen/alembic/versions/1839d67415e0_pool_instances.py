"""Пул стандартных экземпляров для первого задания сессии

Revision ID: 1839d67415e0
Revises: f9713012124f
Create Date: 2026-10-06 20:50:18.938984
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "1839d67415e0"
down_revision: str | Sequence[str] | None = "f9713012124f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pool_instances",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("template_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("public_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("hidden_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("generated_by", sa.String(length=100), nullable=False),
        sa.Column("validated", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["template_id"], ["taskgen.templates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        schema="taskgen",
    )
    op.create_index(
        op.f("ix_taskgen_pool_instances_template_id"),
        "pool_instances",
        ["template_id"],
        unique=False,
        schema="taskgen",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_taskgen_pool_instances_template_id"), table_name="pool_instances", schema="taskgen"
    )
    op.drop_table("pool_instances", schema="taskgen")
