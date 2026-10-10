"""search_results.factors: разложение выдачи по факторам

Revision ID: b7e2c4d91f30
Revises: a81dac834799
Create Date: 2026-10-10 18:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'b7e2c4d91f30'
down_revision: Union[str, None] = 'a81dac834799'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'search_results',
        sa.Column('factors', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema='match',
    )


def downgrade() -> None:
    op.drop_column('search_results', 'factors', schema='match')
