from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = {"schema": "hire"}
    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("uuidv7()"))
    invitation_id: Mapped[UUID] = mapped_column(
        ForeignKey("hire.invitations.id", ondelete="CASCADE"), unique=True
    )
    employer_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("hire.employers.user_id", ondelete="CASCADE"), index=True
    )
    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("profile.candidates.user_id", ondelete="CASCADE"), index=True
    )
    company_id: Mapped[UUID] = mapped_column(ForeignKey("hire.companies.id", ondelete="CASCADE"))
    employer_read_id: Mapped[int] = mapped_column(BigInteger, server_default="0")
    candidate_read_id: Mapped[int] = mapped_column(BigInteger, server_default="0")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sender_user_id", "client_message_id"),
        CheckConstraint(
            "(is_offer AND content IS NULL AND client_message_id IS NULL) OR (NOT is_offer AND content IS NOT NULL AND char_length(btrim(content)) BETWEEN 1 AND 5000 AND client_message_id IS NOT NULL)",
            name="content_kind",
        ),
        {"schema": "hire"},
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("hire.conversations.id", ondelete="CASCADE")
    )
    sender_user_id: Mapped[UUID] = mapped_column(ForeignKey("core.users.id", ondelete="CASCADE"))
    client_message_id: Mapped[UUID | None]
    content: Mapped[str | None] = mapped_column(Text)
    is_offer: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("messages_conversation_cursor_idx", Message.conversation_id, Message.id)
Index("messages_offer_uq", Message.conversation_id, unique=True, postgresql_where=text("is_offer"))
