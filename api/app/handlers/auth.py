import asyncio
import secrets
from datetime import timedelta
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common import Input
from app.db.enums import ConsentKind, UserRole, UserStatus
from app.db.models.core import RefreshSession, User, UserConsent, UserRoleLink
from app.db.models.profile import Candidate, CandidatePreferences, CandidatePrivacy, FspProfile
from app.db.session import get_session
from app.schemas import RegistrationOut, TokenOut
from app.security import (
    authenticated_user,
    encode_access,
    hash_token,
    hasher,
    new_token,
    now,
    verify_password,
)

router = APIRouter(tags=["Авторизация"])


class Register(Input):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    roles: list[Literal["candidate", "employer"]] = Field(min_length=1, max_length=2)
    first_name: str = Field(min_length=1, max_length=50)
    last_name: str = Field(min_length=1, max_length=50)
    accept_processing: Literal[True]


class Login(Input):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


def cookies(response, request, token, csrf):
    settings = request.app.state.settings
    response.set_cookie(
        "fsp_refresh",
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/auth",
        max_age=settings.refresh_token_days * 86400,
    )
    response.set_cookie(
        "fsp_csrf",
        csrf,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.refresh_token_days * 86400,
    )
    response.headers["Cache-Control"] = "no-store"


def csrf_check(request):
    origin = request.headers.get("origin")
    settings = request.app.state.settings
    if origin and origin not in settings.cors_origins:
        raise HTTPException(403, "Источник запроса запрещён")
    cookie, header = request.cookies.get("fsp_csrf"), request.headers.get("x-csrf-token")
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        raise HTTPException(403, "Недействительный CSRF-токен")


@router.post(
    "/auth/register",
    status_code=201,
    response_model=RegistrationOut,
    summary="Зарегистрировать аккаунт",
)
async def register(body: Register, session: AsyncSession = Depends(get_session)):
    user = User(
        id=uuid4(),
        email=str(body.email).lower(),
        status=UserStatus.PENDING,
        password_hash=await asyncio.to_thread(hasher.hash, body.password),
    )
    session.add(user)
    await session.flush()
    session.add_all(
        [UserRoleLink(user_id=user.id, role=UserRole(role)) for role in set(body.roles)]
    )
    session.add(UserConsent(user_id=user.id, kind=ConsentKind.PD_PROCESSING, document_version="1"))
    if "candidate" in body.roles:
        session.add(
            Candidate(user_id=user.id, first_name=body.first_name, last_name=body.last_name)
        )
        await session.flush()
        session.add_all(
            [
                CandidatePreferences(candidate_id=user.id),
                CandidatePrivacy(candidate_id=user.id, is_searchable=False),
                FspProfile(candidate_id=user.id),
            ]
        )
    await session.commit()
    return {"id": user.id, "status": user.status, "email_verified": False}


@router.post(
    "/auth/login",
    response_model=TokenOut,
    response_model_exclude_unset=True,
    summary="Войти по почте и паролю",
)
async def login(
    body: Login, request: Request, response: Response, session: AsyncSession = Depends(get_session)
):
    user = await session.scalar(select(User).where(User.email == str(body.email).lower()))
    encoded = (
        user.password_hash if user and user.password_hash else request.app.state.dummy_password_hash
    )
    valid = await asyncio.to_thread(verify_password, body.password, encoded)
    if not user or not valid:
        raise HTTPException(401, "Неверная почта или пароль")
    if user.status == UserStatus.BLOCKED:
        raise HTTPException(403, "Аккаунт заблокирован")
    token, csrf = new_token(), new_token()
    settings = request.app.state.settings
    refresh = RefreshSession(
        id=uuid4(),
        user_id=user.id,
        token_hash=hash_token(token),
        expires_at=now() + timedelta(days=settings.refresh_token_days),
        user_agent=request.headers.get("user-agent", "")[:255],
    )
    session.add(refresh)
    user.last_login_at = now()
    await session.commit()
    cookies(response, request, token, csrf)
    return {
        "access_token": encode_access(user.id, refresh.id, settings),
        "token_type": "bearer",
        "user_id": user.id,
        "email_verified": user.email_verified_at is not None,
    }


@router.post(
    "/auth/refresh",
    response_model=TokenOut,
    response_model_exclude_unset=True,
    summary="Обновить токены с ротацией refresh",
)
async def refresh(
    request: Request, response: Response, session: AsyncSession = Depends(get_session)
):
    csrf_check(request)
    old = request.cookies.get("fsp_refresh", "")
    row = await session.scalar(
        select(RefreshSession).where(RefreshSession.token_hash == hash_token(old)).with_for_update()
    )
    if row is None or row.revoked_at or row.expires_at <= now():
        raise HTTPException(401, "Сессия завершена")
    user = await session.get(User, row.user_id)
    if user.status == UserStatus.BLOCKED:
        raise HTTPException(403, "Аккаунт заблокирован")
    token = new_token()
    row.token_hash = hash_token(token)
    await session.commit()
    cookies(response, request, token, new_token())
    return {
        "access_token": encode_access(user.id, row.id, request.app.state.settings),
        "token_type": "bearer",
    }


@router.post("/auth/logout", summary="Завершить текущую сессию")
async def logout(
    request: Request, response: Response, session: AsyncSession = Depends(get_session)
):
    csrf_check(request)
    row = await session.scalar(
        select(RefreshSession)
        .where(RefreshSession.token_hash == hash_token(request.cookies.get("fsp_refresh", "")))
        .with_for_update()
    )
    if row and row.revoked_at is None:
        row.revoked_at = now()
        await session.commit()
    response.delete_cookie("fsp_refresh", path="/auth")
    response.delete_cookie("fsp_csrf")
    return {"status": "done"}


@router.get("/me", summary="Текущий аккаунт и доступные кабинеты")
async def me(
    request: Request,
    user: User = Depends(authenticated_user),
    session: AsyncSession = Depends(get_session),
):
    roles = (
        await session.scalars(select(UserRoleLink.role).where(UserRoleLink.user_id == user.id))
    ).all()
    return {
        "id": user.id,
        "email": user.email,
        "roles": roles,
        "status": user.status,
        "email_verified": user.email_verified_at is not None,
        "demo_auth": request.app.state.settings.demo_auth_allow_unverified,
        "public_contacts_enabled": request.app.state.settings.allow_public_contacts,
    }
