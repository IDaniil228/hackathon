"""Проверка живости: заодно подтверждает, что слой SQLAlchemy действительно
достаёт данные из развёрнутой схемы, а не просто импортируется."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.profile import Candidate
from app.db.models.ref import Category, Skill
from app.db.session import get_session

router = APIRouter(tags=["service"])


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    candidates = await session.scalar(select(func.count()).select_from(Candidate))
    categories = await session.scalar(select(func.count()).select_from(Category))
    skills = await session.scalar(select(func.count()).select_from(Skill))
    return {
        "status": "ok",
        "candidates": candidates,
        "categories": categories,
        "skills": skills,
    }
