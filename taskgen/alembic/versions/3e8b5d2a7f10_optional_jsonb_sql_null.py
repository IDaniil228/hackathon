"""JSON null в необязательных полях JSONB заменяется на SQL NULL

Модель писала None в необязательное поле JSONB как JSON null. Проверка IS NULL такое поле не
видит: удалённый снимок профиля кандидата выглядел бы как сохранённый. Теперь модель пишет
SQL NULL, а миграция приводит к нему старые строки. Обратная миграция не нужна: JSON null в
этих полях ничего не значит.

Revision ID: 3e8b5d2a7f10
Revises: 7c4e2a91d5f3
Create Date: 2026-10-07 19:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3e8b5d2a7f10"
down_revision: str | Sequence[str] | None = "7c4e2a91d5f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    columns = connection.execute(
        sa.text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'taskgen' AND data_type = 'jsonb' AND is_nullable = 'YES'"
        )
    ).all()
    for table, column in columns:
        op.execute(
            f'UPDATE taskgen."{table}" SET "{column}" = NULL WHERE "{column}" = \'null\'::jsonb'
        )


def downgrade() -> None:
    pass
