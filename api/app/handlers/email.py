import asyncio
import smtplib
import ssl
from datetime import timedelta
from email.message import EmailMessage
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common import Input
from app.db.enums import TokenPurpose, UserStatus
from app.db.models.core import EmailToken, RefreshSession, User
from app.db.session import get_session
from app.security import authenticated_user, hash_token, hasher, new_token, now

router = APIRouter(tags=["Подтверждение почты и восстановление"])


class TokenInput(Input):
    token: str = Field(min_length=20, max_length=200)


class ResetRequest(Input):
    email: EmailStr


class ResetPassword(TokenInput):
    password: str = Field(min_length=12, max_length=128)


def send_email(settings, email, token, purpose):
    message = EmailMessage()
    message["From"] = settings.smtp_sender
    message["To"] = email
    message["Subject"] = (
        "Подтверждение почты ФСП"
        if purpose == TokenPurpose.VERIFY_EMAIL
        else "Восстановление пароля ФСП"
    )
    path = "/verify-email" if purpose == TokenPurpose.VERIFY_EMAIL else "/reset-password"
    message.set_content(
        "Письмо для адреса " + email + "\n\nОткройте ссылку: "
        + settings.frontend_url.rstrip("/")
        + path
        + "?"
        + urlencode({"token": token})
    )
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_starttls:
            smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)


async def issue(session, user, purpose):
    await session.refresh(user, with_for_update=True)
    recent = await session.scalar(
        select(EmailToken)
        .where(
            EmailToken.user_id == user.id,
            EmailToken.purpose == purpose,
            EmailToken.created_at > now() - timedelta(minutes=1),
        )
        .limit(1)
    )
    if recent:
        raise HTTPException(429, "Повторите запрос через минуту")
    token = new_token()
    session.add(
        EmailToken(
            user_id=user.id,
            purpose=purpose,
            token_hash=hash_token(token),
            expires_at=now() + timedelta(hours=1),
        )
    )
    await session.commit()
    return token


@router.post(
    "/auth/email/request", status_code=202, summary="Запросить подтверждение собственной почты"
)
async def request_verification(
    request: Request,
    user: User = Depends(authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    settings = request.app.state.settings
    if not settings.smtp_host and not (
        settings.demo_auth_allow_unverified and settings.demo_email_tokens
    ):
        raise HTTPException(503, "Почтовый сервис не настроен")
    token = await issue(session, user, TokenPurpose.VERIFY_EMAIL)
    if settings.demo_auth_allow_unverified and settings.demo_email_tokens:
        return {"status": "accepted", "demo": True, "token": token}
    try:
        await asyncio.to_thread(send_email, settings, user.email, token, TokenPurpose.VERIFY_EMAIL)
    except (OSError, smtplib.SMTPException) as error:
        raise HTTPException(503, "Не удалось отправить письмо") from error
    return {"status": "accepted"}


async def consume(session, token, purpose):
    row = await session.scalar(
        select(EmailToken).where(
            EmailToken.token_hash == hash_token(token), EmailToken.purpose == purpose
        )
    )
    if row is None or row.used_at or row.expires_at <= now():
        raise HTTPException(422, "Токен недействителен или истёк")
    user = await session.get(User, row.user_id, with_for_update=True)
    await session.refresh(row, with_for_update=True)
    if row.used_at or row.expires_at <= now():
        raise HTTPException(422, "Токен недействителен или истёк")
    if user.status == UserStatus.BLOCKED:
        raise HTTPException(403, "Аккаунт заблокирован")
    row.used_at = now()
    return user


@router.post("/auth/email/verify", summary="Подтвердить почту одноразовым токеном")
async def verify(body: TokenInput, session: AsyncSession = Depends(get_session)):
    user = await consume(session, body.token, TokenPurpose.VERIFY_EMAIL)
    user.email_verified_at, user.status = now(), UserStatus.ACTIVE
    await session.commit()
    return {"status": "done"}


@router.post(
    "/auth/password/request",
    status_code=202,
    summary="Запросить восстановление без раскрытия существования аккаунта",
)
async def reset_request(
    body: ResetRequest, request: Request, session: AsyncSession = Depends(get_session)
):
    settings = request.app.state.settings
    if not settings.smtp_host:
        raise HTTPException(503, "Почтовый сервис не настроен")
    user = await session.scalar(select(User).where(User.email == str(body.email).lower()))
    if user and user.status != UserStatus.BLOCKED:
        try:
            token = await issue(session, user, TokenPurpose.RESET_PASSWORD)
            await asyncio.to_thread(
                send_email, settings, user.email, token, TokenPurpose.RESET_PASSWORD
            )
        except (HTTPException, OSError, smtplib.SMTPException):
            pass
    return {"status": "accepted"}


@router.post("/auth/password/reset", summary="Заменить пароль и отозвать все сессии")
async def reset(body: ResetPassword, session: AsyncSession = Depends(get_session)):
    encoded = await asyncio.to_thread(hasher.hash, body.password)
    user = await consume(session, body.token, TokenPurpose.RESET_PASSWORD)
    user.password_hash = encoded
    await session.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=now())
    )
    await session.execute(
        update(EmailToken)
        .where(
            EmailToken.user_id == user.id,
            EmailToken.purpose == TokenPurpose.RESET_PASSWORD,
            EmailToken.used_at.is_(None),
        )
        .values(used_at=now())
    )
    await session.commit()
    return {"status": "done"}
