"""Атомарная корреляция прогонов с запросами клиентов"""

import sqlalchemy as sa
from alembic import op

revision = "5ab75e613b18"
down_revision = "31638889b41c"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "run_requests",
        sa.Column("client_hash", sa.String(64), primary_key=True),
        sa.Column("request_ref", sa.UUID(), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        schema="matcher",
    )


def downgrade():
    op.drop_table("run_requests", schema="matcher")
