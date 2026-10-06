"""Статус задания и замены шаблона

Задания создаются вместе с сессией в статусе pending и получают условие позже, поэтому
колонки содержимого становятся необязательными, а ограничение tasks_ready_has_content
требует их у готового задания. Существующие задания уже сгенерированы и получают ready.

Revision ID: f9713012124f
Revises: e750da378937
Create Date: 2026-10-06 20:44:02.622849
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f9713012124f"
down_revision: str | Sequence[str] | None = "e750da378937"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONTENT = ("statement", "params", "public_payload", "hidden_payload", "generated_by")
CONTENT_TYPES = {
    "statement": sa.TEXT(),
    "params": postgresql.JSONB(astext_type=sa.Text()),
    "public_payload": postgresql.JSONB(astext_type=sa.Text()),
    "hidden_payload": postgresql.JSONB(astext_type=sa.Text()),
    "generated_by": sa.VARCHAR(length=100),
}


def upgrade() -> None:
    # Значение по умолчанию нужно только существующим строкам; новые получают статус от кода.
    op.add_column(
        "tasks",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ready"),
        schema="taskgen",
    )
    op.alter_column("tasks", "status", server_default=None, schema="taskgen")
    op.add_column("tasks", sa.Column("failure", sa.Text(), nullable=True), schema="taskgen")
    op.add_column(
        "tasks",
        sa.Column(
            "replacements",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        schema="taskgen",
    )
    for column in CONTENT:
        op.alter_column(
            "tasks", column, existing_type=CONTENT_TYPES[column], nullable=True, schema="taskgen"
        )
    op.create_check_constraint(
        "tasks_ready_has_content",
        "tasks",
        "status <> 'ready' OR (" + " AND ".join(f"{c} IS NOT NULL" for c in CONTENT) + ")",
        schema="taskgen",
    )
    op.create_unique_constraint(
        "tasks_session_id_position_key", "tasks", ["session_id", "position"], schema="taskgen"
    )


def downgrade() -> None:
    # Старая схема не умеет хранить незаконченное задание: такие задания удаляются, а их
    # сессии получают failed, чтобы их не оценили по неполному набору.
    op.execute(
        "UPDATE taskgen.test_sessions SET status = 'failed', "
        "failure = coalesce(failure, 'Задания не сгенерированы до отката схемы') "
        "WHERE id IN (SELECT session_id FROM taskgen.tasks WHERE status <> 'ready')"
    )
    op.execute("DELETE FROM taskgen.tasks WHERE status <> 'ready'")
    op.drop_constraint("tasks_session_id_position_key", "tasks", schema="taskgen", type_="unique")
    op.drop_constraint("tasks_ready_has_content", "tasks", schema="taskgen", type_="check")
    for column in reversed(CONTENT):
        op.alter_column(
            "tasks", column, existing_type=CONTENT_TYPES[column], nullable=False, schema="taskgen"
        )
    op.drop_column("tasks", "replacements", schema="taskgen")
    op.drop_column("tasks", "failure", schema="taskgen")
    op.drop_column("tasks", "status", schema="taskgen")
