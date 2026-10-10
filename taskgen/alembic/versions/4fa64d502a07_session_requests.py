"""Атомарная корреляция создания сессий с запросами клиентов"""

from alembic import op
import sqlalchemy as sa

revision = "4fa64d502a07"
down_revision = "3e8b5d2a7f10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "session_requests",
        sa.Column("client_hash", sa.String(64), primary_key=True),
        sa.Column("request_ref", sa.UUID(), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "session_id",
            sa.UUID(),
            sa.ForeignKey("taskgen.test_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        schema="taskgen",
    )


def downgrade():
    op.drop_table("session_requests", schema="taskgen")
