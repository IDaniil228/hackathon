"""Переписка по предложениям работодателей

Revision ID: f1c64d503a07
Revises: e0b53c4f2196
Create Date: 2026-10-10 22:24:28.020443
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1c64d503a07"
down_revision: Union[str, None] = "e0b53c4f2196"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("invitation_id", sa.UUID(), nullable=False),
        sa.Column("employer_user_id", sa.UUID(), nullable=False),
        sa.Column("candidate_id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("employer_read_id", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("candidate_read_id", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["profile.candidates.user_id"],
            name=op.f("conversations_candidate_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["hire.companies.id"],
            name=op.f("conversations_company_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["employer_user_id"],
            ["hire.employers.user_id"],
            name=op.f("conversations_employer_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invitation_id"],
            ["hire.invitations.id"],
            name=op.f("conversations_invitation_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("conversations_pkey")),
        sa.UniqueConstraint("invitation_id", name=op.f("conversations_invitation_id_key")),
        schema="hire",
    )
    op.create_index(
        op.f("conversations_candidate_id_idx"),
        "conversations",
        ["candidate_id"],
        unique=False,
        schema="hire",
    )
    op.create_index(
        op.f("conversations_employer_user_id_idx"),
        "conversations",
        ["employer_user_id"],
        unique=False,
        schema="hire",
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("sender_user_id", sa.UUID(), nullable=False),
        sa.Column("client_message_id", sa.Uuid(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("is_offer", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(is_offer AND content IS NULL AND client_message_id IS NULL) OR (NOT is_offer AND content IS NOT NULL AND char_length(btrim(content)) BETWEEN 1 AND 5000 AND client_message_id IS NOT NULL)",
            name=op.f("messages_content_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["hire.conversations.id"],
            name=op.f("messages_conversation_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sender_user_id"],
            ["core.users.id"],
            name=op.f("messages_sender_user_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("messages_pkey")),
        sa.UniqueConstraint(
            "conversation_id",
            "sender_user_id",
            "client_message_id",
            name=op.f("messages_conversation_id_sender_user_id_client_message_id_key"),
        ),
        schema="hire",
    )
    op.create_index(
        "messages_conversation_cursor_idx",
        "messages",
        ["conversation_id", "id"],
        unique=False,
        schema="hire",
    )
    op.create_index(
        "messages_offer_uq",
        "messages",
        ["conversation_id"],
        unique=True,
        schema="hire",
        postgresql_where=sa.text("is_offer"),
    )


def downgrade() -> None:
    op.drop_index("messages_offer_uq", table_name="messages", schema="hire")
    op.drop_index("messages_conversation_cursor_idx", table_name="messages", schema="hire")
    op.drop_table("messages", schema="hire")
    op.drop_index(
        op.f("conversations_employer_user_id_idx"), table_name="conversations", schema="hire"
    )
    op.drop_index(op.f("conversations_candidate_id_idx"), table_name="conversations", schema="hire")
    op.drop_table("conversations", schema="hire")
