from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common import Input, found, record
from app.db import enums
from app.db.models.backend import FspDemoCode
from app.db.models.core import User
from app.db.models.profile import FspAchievement, FspEvent, FspProfile
from app.db.session import get_session
from app.handlers.profiles import candidate_for
from app.security import current_user, hash_token, new_token, now
from app.visibility import has_consent

router = APIRouter(tags=["Демонстрационная интеграция ФСП"])


@router.get("/me/fsp", summary="Состояние привязки ФСП, включая отсутствие истории")
async def profile(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return record(await found(session, FspProfile, user.id))


class DemoParticipant(Input):
    history: Literal["empty", "team_prize"] = "empty"


class LinkCode(Input):
    code: str


def demo_only(request):
    if not request.app.state.settings.fsp_demo_enabled:
        raise HTTPException(503, "Реестр ФСП не настроен")


@router.post("/me/fsp/demo-code", summary="Получить одноразовый код синтетического реестра")
async def demo_code(
    body: DemoParticipant,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    demo_only(request)
    await candidate_for(session, user)
    if not await has_consent(session, user.id, enums.ConsentKind.FSP_DATA_SYNC):
        raise HTTPException(409, "Требуется согласие на синхронизацию ФСП")
    code = new_token()
    session.add(
        FspDemoCode(
            token_hash=hash_token(code),
            candidate_id=user.id,
            participant=f"demo:{user.id}:{body.history}",
            expires_at=now() + timedelta(minutes=10),
        )
    )
    await session.commit()
    return {
        "code": code,
        "demo": True,
        "note": "Это не подтверждение личности федерацией и не вход через FSP ID",
    }


async def sync_demo(session, profile):
    await session.execute(
        delete(FspAchievement).where(FspAchievement.candidate_id == profile.candidate_id)
    )
    if profile.fsp_participant_id.endswith("team_prize"):
        event = await session.scalar(
            select(FspEvent).where(FspEvent.external_id == "demo-team-2026")
        )
        if event is None:
            event = FspEvent(
                external_id="demo-team-2026",
                title="Демонстрационный командный хакатон",
                discipline="Хакатон",
                level=enums.FspEventLevel.REGIONAL,
                starts_on=date(2026, 9, 1),
            )
            session.add(event)
            await session.flush()
        session.add(
            FspAchievement(
                candidate_id=profile.candidate_id,
                event_id=event.id,
                place=2,
                participants=30,
                team_name="Демонстрационная команда",
                source=enums.AchievementSource.REGISTRY,
                confirmed_at=now(),
                raw={"demo": True, "participant": profile.fsp_participant_id},
            )
        )
    profile.raw_payload = {"demo": True, "participant": profile.fsp_participant_id}
    profile.synced_at = now()


@router.post("/me/fsp/link", summary="Привязать демонстрационный профиль одноразовым кодом")
async def link(
    body: LinkCode,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    demo_only(request)
    await candidate_for(session, user)
    if not await has_consent(session, user.id, enums.ConsentKind.FSP_DATA_SYNC):
        raise HTTPException(409, "Требуется согласие на синхронизацию ФСП")
    code = await session.get(FspDemoCode, hash_token(body.code), with_for_update=True)
    if code is None or code.candidate_id != user.id or code.used_at or code.expires_at <= now():
        raise HTTPException(422, "Код недействителен")
    profile = await found(session, FspProfile, user.id, lock=True)
    profile.fsp_participant_id = code.participant
    profile.verification_status = enums.FspLinkStatus.VERIFIED
    profile.linked_at = now()
    code.used_at = now()
    await sync_demo(session, profile)
    await session.commit()
    return {"profile": record(profile), "demo": True}


@router.post("/me/fsp/sync", summary="Повторно синхронизировать синтетический реестр")
async def sync(
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    demo_only(request)
    if not await has_consent(session, user.id, enums.ConsentKind.FSP_DATA_SYNC):
        raise HTTPException(409, "Требуется согласие на синхронизацию ФСП")
    profile = await found(session, FspProfile, user.id, lock=True)
    if not profile.fsp_participant_id or not profile.fsp_participant_id.startswith("demo:"):
        raise HTTPException(409, "Демонстрационный профиль не привязан")
    await sync_demo(session, profile)
    await session.commit()
    return {"profile": record(profile), "demo": True}


@router.delete("/me/fsp/link", summary="Отвязать ФСП и удалить сигналы реестра из будущего подбора")
async def unlink(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    profile = await found(session, FspProfile, user.id, lock=True)
    profile.fsp_participant_id = None
    profile.raw_payload = None
    profile.verification_status = enums.FspLinkStatus.UNLINKED
    await session.execute(delete(FspAchievement).where(FspAchievement.candidate_id == user.id))
    await session.commit()
    return {"status": "done"}
