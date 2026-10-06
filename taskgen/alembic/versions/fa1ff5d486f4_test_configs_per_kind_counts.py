"""Состав теста по типам заданий и квота заявленного грейда в test_configs

Одно число заданий tasks_per_test заменяют числа заданий каждого типа и квота
min_claimed_per_kind. Прежнее значение не переносится: общее число не говорит, как его
делить между типами. Пустое поле, как и раньше, берёт значение из настроек.

Revision ID: fa1ff5d486f4
Revises: 3b0bfbc2eded
Create Date: 2026-10-07 00:26:39.648133
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fa1ff5d486f4"
down_revision: str | Sequence[str] | None = "3b0bfbc2eded"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ("code_tasks", "essay_tasks", "quiz_tasks", "min_claimed_per_kind")


def upgrade() -> None:
    for name in COLUMNS:
        op.add_column(
            "test_configs", sa.Column(name, sa.SmallInteger(), nullable=True), schema="taskgen"
        )
    op.drop_column("test_configs", "tasks_per_test", schema="taskgen")


def downgrade() -> None:
    op.add_column(
        "test_configs",
        sa.Column("tasks_per_test", sa.SmallInteger(), nullable=True),
        schema="taskgen",
    )
    for name in reversed(COLUMNS):
        op.drop_column("test_configs", name, schema="taskgen")
