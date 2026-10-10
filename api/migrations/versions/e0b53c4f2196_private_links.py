"""Новые ссылки требуют явного разрешения на публикацию"""

from alembic import op
import sqlalchemy as sa

revision = "e0b53c4f2196"
down_revision = "d9a42b3e1085"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("candidate_links", "is_public", schema="profile", server_default=sa.text("false"))
    op.execute("UPDATE profile.candidate_links SET is_public = false")


def downgrade():
    op.alter_column("candidate_links", "is_public", schema="profile", server_default=sa.text("true"))
