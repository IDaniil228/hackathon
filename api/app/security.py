import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import UserRole, UserStatus
from app.db.models.core import RefreshSession, User, UserRoleLink
from app.db.session import get_session
from app.settings import Settings

hasher = PasswordHasher()
bearer = HTTPBearer(auto_error=False)


def now():
    return datetime.now(UTC)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(48)


def verify_password(password: str, encoded: str | None) -> bool:
    try:
        return encoded is not None and hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def encode_access(user: UUID, session: UUID, settings: Settings) -> str:
    return jwt.encode(
        {
            "sub": str(user),
            "sid": str(session),
            "iss": settings.jwt_issuer,
            "aud": "fsp-browser",
            "iat": now(),
            "exp": now() + timedelta(minutes=settings.access_token_minutes),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def decode_access(token: str, settings: Settings) -> dict:
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience="fsp-browser",
            options={"require": ["sub", "sid", "iat", "exp", "iss", "aud"]},
        )
    except jwt.PyJWTError as error:
        raise HTTPException(401, "Недействительный токен") from error


def eligible(user: User, settings: Settings) -> bool:
    return user.status == UserStatus.ACTIVE or (
        settings.demo_auth_allow_unverified and user.status == UserStatus.PENDING
    )


async def authenticated_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    if credentials is None:
        raise HTTPException(401, "Требуется вход")
    claims = decode_access(credentials.credentials, request.app.state.settings)
    try:
        user_id, session_id = UUID(claims["sub"]), UUID(claims["sid"])
    except (ValueError, TypeError) as error:
        raise HTTPException(401, "Недействительный токен") from error
    user = await session.get(User, user_id)
    refresh = await session.get(RefreshSession, session_id)
    if (
        user is None
        or refresh is None
        or refresh.user_id != user_id
        or refresh.revoked_at
        or refresh.expires_at <= now()
    ):
        raise HTTPException(401, "Сессия завершена")
    if user.status == UserStatus.BLOCKED:
        raise HTTPException(403, "Аккаунт заблокирован")
    return user


async def current_user(request: Request, user: User = Depends(authenticated_user)):
    if not eligible(user, request.app.state.settings):
        raise HTTPException(403, "Сначала подтвердите почту")
    return user


async def require_role(session: AsyncSession, user: User, role: UserRole) -> None:
    if await session.get(UserRoleLink, (user.id, role)) is None:
        raise HTTPException(403, "Недостаточно прав")


async def employer_for(session, user):
    from app.db.models.hire import Employer

    await require_role(session, user, UserRole.EMPLOYER)
    employer = await session.get(Employer, user.id)
    if employer is None:
        raise HTTPException(409, "Сначала создайте компанию")
    return employer
