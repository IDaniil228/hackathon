"""Причина сбоя сессии в своей колонке

Раньше причина лежала в test_sessions.selection под ключом failure. Миграция переносит её
в колонку failure и убирает ключ из объяснения отбора; откат возвращает как было.

Revision ID: e750da378937
Revises: 102a7a95ef6c
Create Date: 2026-10-06 20:36:01.882840
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e750da378937"
down_revision: str | Sequence[str] | None = "102a7a95ef6c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("test_sessions", sa.Column("failure", sa.Text(), nullable=True), schema="taskgen")
    op.execute(
        "UPDATE taskgen.test_sessions "
        "SET failure = selection->>'failure', selection = selection - 'failure' "
        "WHERE selection ? 'failure'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE taskgen.test_sessions "
        "SET selection = selection || jsonb_build_object('failure', failure) "
        "WHERE failure IS NOT NULL AND selection IS NOT NULL"
    )
    op.drop_column("test_sessions", "failure", schema="taskgen")
