from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ServiceOperation(Base):
    __tablename__ = "service_operations"
    __table_args__ = (UniqueConstraint("owner_id", "kind", "key"), {"schema": "core"})
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("core.users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(40))
    key: Mapped[str] = mapped_column(String(100))
    fingerprint: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[UUID | None]
    remote_id: Mapped[UUID | None]
    state: Mapped[str] = mapped_column(String(30), server_default="queued")
    error: Mapped[str | None] = mapped_column(String(200))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    response: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TestBridge(Base):
    __tablename__ = "test_bridges"
    __table_args__ = ({"schema": "assess"},)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("assess.test_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    remote_id: Mapped[UUID | None] = mapped_column(unique=True)
    area: Mapped[str] = mapped_column(String(40))
    grade: Mapped[str] = mapped_column(String(40))
    state: Mapped[str] = mapped_column(String(30), server_default="queued")
    result: Mapped[dict | None] = mapped_column(JSONB)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FspDemoCode(Base):
    __tablename__ = "fsp_demo_codes"
    __table_args__ = {"schema": "profile"}
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("profile.candidates.user_id", ondelete="CASCADE")
    )
    participant: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConsentEvent(Base):
    __tablename__ = "public_contact_events"
    __table_args__ = {"schema": "profile"}
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("profile.candidates.user_id", ondelete="CASCADE")
    )
    granted: Mapped[bool] = mapped_column(Boolean)
    version: Mapped[str] = mapped_column(String(20), server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmployerTask(Base):
    __tablename__ = "employer_task_owners"
    __table_args__ = {"schema": "assess"}
    template_id: Mapped[int] = mapped_column(
        ForeignKey("assess.task_templates.id", ondelete="CASCADE"), primary_key=True
    )
    company_id: Mapped[UUID] = mapped_column(ForeignKey("hire.companies.id", ondelete="CASCADE"))
    feedback: Mapped[str | None] = mapped_column(Text)


Index(
    "test_bridges_unapplied_idx", TestBridge.state, postgresql_where=TestBridge.applied_at.is_(None)
)
