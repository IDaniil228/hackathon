"""core — пользователи, аутентификация, согласия 152-ФЗ.

Регистрация выполняется по e-mail с подтверждением адреса. Поле
``users.external_subject`` заранее рассчитано на ФСП ID поверх Keycloak: когда
появится реальный IdP, достаточно заполнить subject, а не мигрировать схему.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import enums
from app.db.base import Base

EMAIL_RE = r"^[^@[:space:]]+@[^@[:space:]]+\.[A-Za-z]{2,}$"
SHA256_RE = r"^[a-f0-9]{64}$"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("char_length(email) BETWEEN 5 AND 254", name="email_len"),
        CheckConstraint(f"email ~ '{EMAIL_RE}'", name="email_format"),
        # Пользователь приходит либо с паролем, либо из внешнего IdP
        CheckConstraint(
            "password_hash IS NOT NULL OR external_subject IS NOT NULL",
            name="auth_method_present",
        ),
        # Активировать аккаунт можно только после подтверждения адреса
        CheckConstraint(
            "status <> 'active' OR email_verified_at IS NOT NULL",
            name="active_requires_verified_email",
        ),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()")
    )
    # citext: регистронезависимая уникальность без функционального индекса
    email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    # argon2id укладывается в ~100 символов, запас на смену алгоритма
    password_hash: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[enums.UserStatus] = mapped_column(
        enums.user_status, nullable=False, server_default=text("'pending'")
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: subject внешнего IdP (ФСП ID на базе Keycloak)
    external_subject: Mapped[str | None] = mapped_column(String(64), unique=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserRoleLink(Base):
    """Роль вынесена в отдельную таблицу: один человек может быть и кандидатом,
    и работодателем — например, тимлид, который сам ищет работу и сам нанимает."""

    __tablename__ = "user_roles"
    __table_args__ = {"schema": "core"}

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[enums.UserRole] = mapped_column(enums.user_role, primary_key=True)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EmailToken(Base):
    """Одноразовые токены подтверждения адреса и сброса пароля.

    Хранится SHA-256 от токена, а не сам токен: утечка таблицы не даёт входа.
    """

    __tablename__ = "email_tokens"
    __table_args__ = (
        CheckConstraint(f"token_hash ~ '{SHA256_RE}'", name="hash_format"),
        CheckConstraint("expires_at > created_at", name="expires_after_create"),
        CheckConstraint("used_at IS NULL OR used_at >= created_at", name="used_after_create"),
        {"schema": "core"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[enums.TokenPurpose] = mapped_column(enums.token_purpose, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RefreshSession(Base):
    """Refresh-токены по устройствам: даёт отзыв сессии и «выйти везде»."""

    __tablename__ = "refresh_sessions"
    __table_args__ = (
        CheckConstraint(f"token_hash ~ '{SHA256_RE}'", name="hash_format"),
        CheckConstraint("expires_at > created_at", name="expires_after_create"),
        {"schema": "core"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_agent: Mapped[str | None] = mapped_column(String(255))
    ip: Mapped[str | None] = mapped_column(INET)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserConsent(Base):
    """Согласия по 152-ФЗ.

    Таблица append-only: отзыв согласия — это не DELETE, а проставленный
    ``revoked_at``. Удалив строку, мы потеряли бы доказательство, что согласие
    вообще давалось, а именно его и спросят при разборе претензии.
    """

    __tablename__ = "user_consents"
    __table_args__ = (
        CheckConstraint("char_length(btrim(document_version)) > 0", name="doc_version_not_blank"),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= granted_at", name="revoked_after_granted"
        ),
        {"schema": "core"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[enums.ConsentKind] = mapped_column(enums.consent_kind, nullable=False)
    #: редакция документа, с которой согласился пользователь
    document_version: Mapped[str] = mapped_column(String(20), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- индексы -----------------------------------------------------------------
# Партиальные индексы вместо полных: эти запросы всегда идут с одним и тем же
# условием, а индекс получается меньше и не обслуживает лишние строки.

#: Действующее согласие каждого вида может быть только одно.
#: Партиальный UNIQUE заменяет здесь триггер-сторож.
Index(
    "user_consents_active_uq",
    UserConsent.user_id,
    UserConsent.kind,
    unique=True,
    postgresql_where=UserConsent.revoked_at.is_(None),
)

#: Живые токены при подтверждении адреса
Index(
    "email_tokens_active_idx",
    EmailToken.user_id,
    EmailToken.purpose,
    postgresql_where=EmailToken.used_at.is_(None),
)

#: Активные сессии пользователя
Index(
    "refresh_sessions_user_idx",
    RefreshSession.user_id,
    postgresql_where=RefreshSession.revoked_at.is_(None),
)

Index("users_status_idx", User.status)
Index("user_roles_role_idx", UserRoleLink.role)
