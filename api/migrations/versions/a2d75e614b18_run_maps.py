import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a2d75e614b18"
down_revision = "f1c64d503a07"
branch_labels = None
depends_on = None
LOCK_FUNCTION = """
CREATE OR REPLACE FUNCTION profile.lock_candidate(candidate uuid) RETURNS void
LANGUAGE sql AS $$
SELECT pg_advisory_xact_lock(hashtextextended('profile:' || candidate::text, 0))
$$
"""


def upgrade():
    op.execute(LOCK_FUNCTION)
    op.create_table(
        "run_map_snapshots",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["match.search_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
        schema="match",
    )


def downgrade():
    op.execute("DROP FUNCTION profile.lock_candidate(uuid)")
    op.drop_table("run_map_snapshots", schema="match")
