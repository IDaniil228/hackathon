"""Сервисные ключи с ролями.

Ключ выдаётся один раз, в БД лежит только его SHA-256. Кандидат в сервис напрямую не ходит:
ключ client держит основной бэкенд, ключ admin — тот, кто загружает шаблоны и настройки.
"""

from __future__ import annotations

import hashlib
import secrets
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy import select

from taskgen.db import ApiKey


class Role(StrEnum):
    ADMIN = "admin"
    CLIENT = "client"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


_bearer = HTTPBearer(auto_error=False)


async def current_role(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Role:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Нужен заголовок Authorization: Bearer")
    key = credentials.credentials
    settings = request.app.state.settings
    if settings.bootstrap_admin_key and secrets.compare_digest(key, settings.bootstrap_admin_key):
        return Role.ADMIN
    async with request.app.state.sessionmaker() as session:
        row = await session.scalar(
            select(ApiKey).where(ApiKey.key_hash == hash_key(key), ApiKey.revoked_at.is_(None))
        )
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Ключ не найден или отозван")
    return Role(row.role)


def require(*roles: Role):
    """Зависимость FastAPI: пропускает только перечисленные роли. Admin проходит всегда."""

    async def check(role: Annotated[Role, Depends(current_role)]) -> Role:
        if role is not Role.ADMIN and role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав для операции")
        return role

    return check


router = APIRouter(prefix="/keys", tags=["Ключи"])


class KeyCreate(BaseModel):
    name: str
    role: Role


class KeyIssued(BaseModel):
    id: int
    name: str
    role: Role
    key: str


@router.post(
    "",
    response_model=KeyIssued,
    status_code=status.HTTP_201_CREATED,
    summary="Выпустить сервисный ключ",
    description="Ключ возвращается один раз; сервис хранит только его хэш",
    dependencies=[Depends(require(Role.ADMIN))],
)
async def issue_key(body: KeyCreate, request: Request) -> KeyIssued:
    key = secrets.token_urlsafe(32)
    async with request.app.state.sessionmaker() as session, session.begin():
        row = ApiKey(name=body.name, role=body.role.value, key_hash=hash_key(key))
        session.add(row)
        await session.flush()
        return KeyIssued(id=row.id, name=row.name, role=body.role, key=key)
