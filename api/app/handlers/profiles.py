from datetime import date
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy import exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common import Input, assign, found, record
from app.db import enums
from app.db.models.assess import GradeAssignment
from app.db.models.backend import ConsentEvent
from app.db.models.core import User, UserConsent
from app.db.models.profile import (
    Candidate,
    CandidateLink,
    CandidatePreferences,
    CandidatePrivacy,
    CandidateSkill,
)
from app.db.models.ref import Category, Grade, Industry, Skill, SkillArea, Specialization
from app.db.session import get_session
from app.operations import queue_operation
from app.schemas import CandidateCard, CandidateList
from app.security import current_user, employer_for, now, require_role
from app.visibility import card, has_consent

router = APIRouter(tags=["Профили и справочники"])


@router.get("/me/disclosures", summary="Компании с действующим доступом к контактам")
async def disclosures(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    from app.db.models.hire import Company, ContactDisclosure

    rows = (
        await session.execute(
            select(ContactDisclosure, Company.name)
            .join(Company)
            .where(
                ContactDisclosure.candidate_id == user.id, ContactDisclosure.revoked_at.is_(None)
            )
        )
    ).all()
    return [{**record(row), "company_name": company_name} for row, company_name in rows]


class ProfileUpdate(Input):
    first_name: str | None = Field(default=None, min_length=1, max_length=50)
    last_name: str | None = Field(default=None, min_length=1, max_length=50)
    middle_name: str | None = Field(default=None, max_length=50)
    birth_date: date | None = None
    phone: str | None = Field(default=None, pattern=r"^\+?[0-9]{7,15}$")
    city: str | None = Field(default=None, max_length=100)
    relocation: bool | None = None
    work_format: enums.WorkFormat | None = None
    employment_type: enums.EmploymentType | None = None
    experience_months: int | None = Field(default=None, ge=0, le=840)
    about: str | None = Field(default=None, max_length=4000)
    is_open_to_offers: bool | None = None


class Preferences(Input):
    desired_specialization_id: int | None = None
    salary_min: int | None = Field(default=None, gt=0, le=100000000)
    salary_max: int | None = Field(default=None, gt=0, le=100000000)
    positive_prompt: str | None = Field(default=None, max_length=1000)
    negative_prompt: str | None = Field(default=None, max_length=1000)


class Privacy(Input):
    is_searchable: bool | None = None
    show_contacts: bool | None = None
    show_salary: bool | None = None
    show_fsp: bool | None = None
    show_github: bool | None = None
    show_name: bool | None = None
    show_city: bool | None = None
    show_experience: bool | None = None
    show_about: bool | None = None
    public_contacts: bool | None = None
    accept_public_contacts: bool = False


class Consent(Input):
    kind: enums.ConsentKind
    granted: bool
    document_version: str = Field(default="1", min_length=1, max_length=20)


class SelfSkill(Input):
    skill_id: int = Field(gt=0)
    self_level: int = Field(ge=0, le=5)


class Skills(Input):
    skills: list[SelfSkill] = Field(max_length=100)


class Link(Input):
    kind: enums.LinkKind
    url: str = Field(max_length=2048, pattern=r"^https?://[^\s]{3,}$")
    label: str | None = Field(default=None, max_length=60)
    is_public: bool = False


async def candidate_for(session, user):
    await require_role(session, user, enums.UserRole.CANDIDATE)
    return await found(session, Candidate, user.id)


@router.get("/references/{name}", summary="Получить справочник")
async def references(
    name: Literal["industries", "specializations", "grades", "categories", "skills", "skill_areas"],
    session: AsyncSession = Depends(get_session),
):
    model = {
        "industries": Industry,
        "specializations": Specialization,
        "grades": Grade,
        "categories": Category,
        "skills": Skill,
        "skill_areas": SkillArea,
    }[name]
    if name == "categories":
        rows = (
            await session.execute(
                select(Category, Specialization.name, Grade.name)
                .join(Specialization)
                .join(Grade)
                .order_by(Category.id)
            )
        ).all()
        return [
            {**record(row), "name": f"{specialization} · {grade}"}
            for row, specialization, grade in rows
        ]
    return [record(row) for row in (await session.scalars(select(model).order_by(model.id))).all()]


@router.get(
    "/me/profile",
    response_model=CandidateCard,
    response_model_exclude_unset=True,
    summary="Собственный профиль",
)
async def my_profile(
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await candidate_for(session, user)
    return await card(session, user.id, request.app.state.settings, owner=True)


@router.patch(
    "/me/profile",
    response_model=CandidateCard,
    response_model_exclude_unset=True,
    summary="Изменить анкету без изменения подтверждённого грейда",
)
async def update_profile(
    body: ProfileUpdate,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    candidate = await candidate_for(session, user)
    values = body.model_dump(exclude_unset=True)
    for field in (
        "first_name",
        "last_name",
        "experience_months",
        "relocation",
        "is_open_to_offers",
    ):
        if field in values and values[field] is None:
            raise HTTPException(422, "Обязательное поле не может быть null")
    assign(candidate, values)
    candidate.updated_at = now()
    await session.commit()
    return await card(session, user.id, request.app.state.settings, owner=True)


@router.put("/me/preferences", summary="Изменить предпочтения и зарплату до вычета НДФЛ")
async def preferences(
    body: Preferences,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await candidate_for(session, user)
    preference = await found(session, CandidatePreferences, user.id, lock=True)
    assign(preference, body.model_dump(exclude_unset=True))
    if (
        preference.salary_min
        and preference.salary_max
        and preference.salary_max < preference.salary_min
    ):
        raise HTTPException(422, "Некорректная зарплатная вилка")
    if preference.desired_specialization_id:
        await found(session, Specialization, preference.desired_specialization_id)
    preference.currency = "RUB"
    preference.updated_at = now()
    await session.commit()
    return record(preference)


@router.put("/me/privacy", summary="Изменить видимость профиля")
async def privacy(
    body: Privacy,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await session.execute(text("SELECT profile.lock_candidate(:id)"), {"id": user.id})
    await candidate_for(session, user)
    row = await found(session, CandidatePrivacy, user.id, lock=True)
    if body.is_searchable and (
        not await has_consent(session, user.id, enums.ConsentKind.PROFILE_PUBLICATION)
        or not await has_consent(session, user.id, enums.ConsentKind.PD_PROCESSING)
    ):
        raise HTTPException(409, "Публикация требует действующих согласий")
    if body.show_fsp and not await has_consent(session, user.id, enums.ConsentKind.FSP_DATA_SYNC):
        raise HTTPException(409, "Показ ФСП требует согласия на синхронизацию")
    if body.public_contacts:
        if not request.app.state.settings.allow_public_contacts or not body.accept_public_contacts:
            raise HTTPException(
                409, "Публичные контакты требуют включённого режима и отдельного согласия"
            )
        row.public_contacts_consented_at = now()
    if body.public_contacts is not None:
        session.add(ConsentEvent(candidate_id=user.id, granted=body.public_contacts))
        if not body.public_contacts:
            row.public_contacts_consented_at = None
    values = body.model_dump(exclude_unset=True, exclude={"accept_public_contacts"})
    if any(value is None for value in values.values()):
        raise HTTPException(422, "Настройки видимости не могут быть null")
    assign(row, values)
    row.updated_at = now()
    await session.commit()
    return record(row)


@router.get("/me/consents", summary="История согласий")
async def consents(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    return [
        record(row)
        for row in (
            await session.scalars(select(UserConsent).where(UserConsent.user_id == user.id))
        ).all()
    ]


@router.post("/me/consents", summary="Выдать или отозвать согласие")
async def consent(
    body: Consent, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    await session.execute(text("SELECT profile.lock_candidate(:id)"), {"id": user.id})
    await session.refresh(user, with_for_update=True)
    rows = (
        await session.scalars(
            select(UserConsent).where(
                UserConsent.user_id == user.id,
                UserConsent.kind == body.kind,
                UserConsent.revoked_at.is_(None),
            )
        )
    ).all()
    for row in rows:
        row.revoked_at = now()
    await session.flush()
    if body.granted:
        session.add(
            UserConsent(user_id=user.id, kind=body.kind, document_version=body.document_version)
        )
    elif body.kind in (
        enums.ConsentKind.PROFILE_PUBLICATION,
        enums.ConsentKind.PD_PROCESSING,
        enums.ConsentKind.FSP_DATA_SYNC,
    ):
        privacy = await session.get(CandidatePrivacy, user.id)
        if privacy:
            if body.kind == enums.ConsentKind.FSP_DATA_SYNC:
                privacy.show_fsp = False
            else:
                privacy.is_searchable = False
    cleanup = None
    if not body.granted and body.kind == enums.ConsentKind.PD_PROCESSING:
        cleanup = await queue_operation(
            session,
            user,
            "map_forget",
            f"forget-{uuid4()}",
            {
                "service": "matcher",
                "path": f"/candidates/{user.id}/maps/revoke",
                "body": {"before": now().isoformat()},
            },
            user.id,
        )
    await session.commit()
    return {
        "kind": body.kind,
        "granted": body.granted,
        "cleanup_operation_id": cleanup.id if cleanup else None,
    }


@router.put("/me/skills", summary="Заявить навыки без самостоятельного подтверждения")
async def skills(
    body: Skills, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    await candidate_for(session, user)
    if len({item.skill_id for item in body.skills}) != len(body.skills):
        raise HTTPException(422, "Навыки не должны повторяться")
    for item in body.skills:
        await found(session, Skill, item.skill_id)
        row = await session.get(CandidateSkill, (user.id, item.skill_id))
        if row is None:
            row = CandidateSkill(candidate_id=user.id, skill_id=item.skill_id)
            session.add(row)
        row.self_level = item.self_level
        row.updated_at = now()
    await session.commit()
    return {"status": "done"}


@router.post("/me/links", status_code=201, summary="Добавить ссылку с явной настройкой видимости")
async def link(
    body: Link, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    await candidate_for(session, user)
    row = CandidateLink(candidate_id=user.id, **body.model_dump())
    session.add(row)
    await session.commit()
    return record(row)


@router.delete("/me/links/{link_id}", summary="Удалить собственную ссылку")
async def unlink(
    link_id: int, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    row = await found(session, CandidateLink, link_id)
    if row.candidate_id != user.id:
        raise HTTPException(404, "Ссылка не найдена")
    await session.delete(row)
    await session.commit()
    return {"status": "done"}


@router.get(
    "/candidates/{candidate_id}",
    response_model=CandidateCard,
    response_model_exclude_unset=True,
    summary="Карточка кандидата для работодателя",
)
async def candidate_card(
    candidate_id: UUID,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    employer = await employer_for(session, user)
    return await card(session, candidate_id, request.app.state.settings, employer.company_id)


@router.get(
    "/candidates",
    response_model=CandidateList,
    response_model_exclude_unset=True,
    summary="Банк доступных кандидатов",
)
async def candidates(
    request: Request,
    category_id: int | None = None,
    specialization_id: int | None = None,
    grade_id: int | None = None,
    grade_confirmed: bool | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    employer = await employer_for(session, user)
    eligible_status = [enums.UserStatus.ACTIVE]
    if request.app.state.settings.demo_auth_allow_unverified:
        eligible_status.append(enums.UserStatus.PENDING)
    query = (
        select(Candidate)
        .outerjoin(Category, Candidate.current_category_id == Category.id)
        .join(CandidatePrivacy, CandidatePrivacy.candidate_id == Candidate.user_id)
        .join(User, User.id == Candidate.user_id)
        .where(
            CandidatePrivacy.is_searchable,
            Candidate.is_open_to_offers,
            User.status.in_(eligible_status),
        )
    )
    for kind in (enums.ConsentKind.PD_PROCESSING, enums.ConsentKind.PROFILE_PUBLICATION):
        query = query.where(
            exists().where(
                UserConsent.user_id == Candidate.user_id,
                UserConsent.kind == kind,
                UserConsent.revoked_at.is_(None),
            )
        )
    if grade_confirmed is not None:
        confirmed = exists().where(
            GradeAssignment.candidate_id == Candidate.user_id,
            GradeAssignment.is_current,
            GradeAssignment.source == enums.GradeSource.TEST,
        )
        query = query.where(confirmed if grade_confirmed else ~confirmed)
    if category_id:
        query = query.where(Candidate.current_category_id == category_id)
    if specialization_id:
        query = query.where(Category.specialization_id == specialization_id)
    if grade_id:
        query = query.where(Category.grade_id == grade_id)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = (
        await session.scalars(
            query.order_by(Candidate.profile_strength.desc(), Candidate.user_id)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    results = []
    for candidate in rows:
        try:
            value = await card(
                session, candidate.user_id, request.app.state.settings, employer.company_id
            )
        except HTTPException as error:
            if error.status_code != 404:
                raise
            continue
        if grade_confirmed is None or value["grade_confirmed"] == grade_confirmed:
            results.append(value)
    return {"total": total, "results": results}
