from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common import Input, assign, found, own_company, record
from app.db import enums
from app.db.models.core import User
from app.db.models.hire import (
    Application,
    ApplicationEvent,
    Company,
    ContactDisclosure,
    Employer,
    EmployerNeed,
    Invitation,
    InvitationEvent,
    NeedSkill,
    Vacancy,
    VacancySkill,
)
from app.db.models.profile import Candidate
from app.db.models.ref import Category, Grade, Skill, Specialization
from app.db.session import get_session
from app.operations import queue_operation
from app.schemas import ApplicationOut, CompanyOut, InvitationOut, NeedOut, VacancyOut
from app.security import current_user, employer_for, now, require_role
from app.visibility import searchable

router = APIRouter(tags=["Найм"])


class CompanyInput(Input):
    name: str = Field(min_length=1, max_length=120)
    full_name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    website: str | None = Field(default=None, max_length=2048, pattern=r"^https?://[^\s]{3,}$")
    city: str | None = Field(default=None, max_length=100)
    inn: str | None = Field(default=None, pattern=r"^(?:[0-9]{10}|[0-9]{12})$")


class RequiredSkill(Input):
    skill_id: int = Field(gt=0)
    importance: enums.SkillImportance = enums.SkillImportance.REQUIRED
    min_level: int | None = Field(default=None, ge=0, le=5)


class NeedInput(Input):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=20000)
    vacancy_id: UUID | None = None
    specialization_id: int | None = None
    grade_id: int | None = None
    category_id: int | None = None
    salary_min: int | None = Field(default=None, gt=0, le=100000000)
    salary_max: int | None = Field(default=None, gt=0, le=100000000)
    positive_prompt: str | None = Field(default=None, max_length=2000)
    negative_prompt: str | None = Field(default=None, max_length=2000)
    is_active: bool = True
    skills: list[RequiredSkill] = Field(default=[], max_length=100)

    @model_validator(mode="after")
    def salary(self):
        if (
            self.salary_min is not None
            and self.salary_max is not None
            and self.salary_max < self.salary_min
        ):
            raise ValueError("Верхняя граница зарплаты ниже нижней")
        return self


class VacancyInput(Input):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=20000)
    category_id: int = Field(gt=0)
    salary_min: int = Field(gt=0, le=100000000)
    salary_max: int = Field(gt=0, le=100000000)
    currency: Literal["RUB"] = "RUB"
    salary_basis: Literal["gross"] = "gross"
    salary_period: Literal["month"] = "month"
    work_format: enums.WorkFormat | None = None
    employment_type: enums.EmploymentType | None = None
    city: str | None = Field(default=None, max_length=100)
    skills: list[RequiredSkill] = Field(default=[], max_length=100)

    @model_validator(mode="after")
    def salary(self):
        if self.salary_max < self.salary_min:
            raise ValueError("Верхняя граница зарплаты ниже нижней")
        return self


class InvitationInput(Input):
    candidate_id: UUID
    vacancy_id: UUID | None = None
    need_id: UUID | None = None
    message: str = Field(min_length=1, max_length=5000)
    salary_min: int = Field(gt=0, le=100000000)
    salary_max: int = Field(gt=0, le=100000000)
    currency: Literal["RUB"] = "RUB"
    salary_basis: Literal["gross"] = "gross"
    salary_period: Literal["month"] = "month"
    contact_method: Literal["email", "phone", "telegram"]
    contact_value: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def salary(self):
        if self.salary_max < self.salary_min:
            raise ValueError("Верхняя граница зарплаты ниже нижней")
        return self


class VacancyState(Input):
    status: enums.VacancyStatus


class ApplyInput(Input):
    cover_letter: str | None = Field(default=None, max_length=5000)


def offer_record(row):
    return {**record(row, ["search_vector"]), "salary_basis": "gross", "salary_period": "month"}


async def replace_skills(session, model, column, resource_id, skills):
    if len({item.skill_id for item in skills}) != len(skills):
        raise HTTPException(422, "Навыки не должны повторяться")
    for item in skills:
        await found(session, Skill, item.skill_id)
    await session.execute(delete(model).where(getattr(model, column) == resource_id))
    session.add_all([model(**{column: resource_id}, **item.model_dump()) for item in skills])


@router.post(
    "/companies",
    status_code=201,
    response_model=CompanyOut,
    summary="Создать компанию и стать владельцем",
)
async def create_company(
    body: CompanyInput,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await require_role(session, user, enums.UserRole.EMPLOYER)
    await session.refresh(user, with_for_update=True)
    if await session.get(Employer, user.id):
        raise HTTPException(409, "Компания уже создана")
    row = Company(id=uuid4(), **body.model_dump(exclude={"full_name"}))
    session.add(row)
    await session.flush()
    session.add(
        Employer(user_id=user.id, company_id=row.id, full_name=body.full_name, is_owner=True)
    )
    await session.commit()
    return record(row)


@router.get("/me/company", response_model=CompanyOut, summary="Собственная компания")
async def my_company(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    employer = await employer_for(session, user)
    return record(await found(session, Company, employer.company_id))


@router.put("/me/company", response_model=CompanyOut, summary="Изменить собственную компанию")
async def update_company(
    body: CompanyInput,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    employer = await employer_for(session, user)
    if not employer.is_owner:
        raise HTTPException(403, "Требуются права владельца")
    row = await found(session, Company, employer.company_id, lock=True)
    assign(row, body.model_dump(exclude={"full_name"}))
    employer.full_name = body.full_name
    row.updated_at = now()
    await session.commit()
    return record(row)


async def validate_need(session, body, employer):
    if body.vacancy_id:
        own_company(await found(session, Vacancy, body.vacancy_id), employer)
    specialization = (
        await found(session, Specialization, body.specialization_id)
        if body.specialization_id
        else None
    )
    grade = await found(session, Grade, body.grade_id) if body.grade_id else None
    if grade and specialization and grade.industry_id != specialization.industry_id:
        raise HTTPException(422, "Грейд и специализация относятся к разным отраслям")
    if body.category_id:
        category = await found(session, Category, body.category_id)
        if (body.specialization_id and category.specialization_id != body.specialization_id) or (
            body.grade_id and category.grade_id != body.grade_id
        ):
            raise HTTPException(422, "Категория не соответствует специализации и грейду")


@router.post(
    "/needs",
    status_code=201,
    response_model=NeedOut,
    summary="Создать потребность без обязательной вакансии",
)
async def create_need(
    body: NeedInput,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    employer = await employer_for(session, user)
    await validate_need(session, body, employer)
    row = EmployerNeed(
        id=uuid4(),
        company_id=employer.company_id,
        author_user_id=user.id,
        **body.model_dump(exclude={"skills"}),
    )
    session.add(row)
    await session.flush()
    await replace_skills(session, NeedSkill, "need_id", row.id, body.skills)
    await session.commit()
    return offer_record(row)


@router.get("/needs", response_model=list[NeedOut], summary="Потребности собственной компании")
async def needs(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    employer = await employer_for(session, user)
    return [
        offer_record(row)
        for row in (
            await session.scalars(
                select(EmployerNeed).where(EmployerNeed.company_id == employer.company_id)
            )
        ).all()
    ]


@router.get("/needs/{need_id}", response_model=NeedOut, summary="Потребность собственной компании")
async def need(
    need_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    employer = await employer_for(session, user)
    row = await found(session, EmployerNeed, need_id)
    own_company(row, employer)
    skills = (await session.scalars(select(NeedSkill).where(NeedSkill.need_id == need_id))).all()
    return {**offer_record(row), "skills": [record(skill) for skill in skills]}


@router.put(
    "/needs/{need_id}", response_model=NeedOut, summary="Изменить потребность собственной компании"
)
async def update_need(
    need_id: UUID,
    body: NeedInput,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    employer = await employer_for(session, user)
    row = await found(session, EmployerNeed, need_id, lock=True)
    own_company(row, employer)
    await validate_need(session, body, employer)
    assign(row, body.model_dump(exclude={"skills"}))
    row.updated_at = now()
    await replace_skills(session, NeedSkill, "need_id", need_id, body.skills)
    await session.commit()
    return offer_record(row)


@router.post(
    "/vacancies",
    status_code=201,
    response_model=VacancyOut,
    summary="Создать черновик вакансии с обязательной зарплатой",
)
async def create_vacancy(
    body: VacancyInput,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    employer = await employer_for(session, user)
    await found(session, Category, body.category_id)
    row = Vacancy(
        id=uuid4(),
        company_id=employer.company_id,
        author_user_id=user.id,
        **body.model_dump(exclude={"skills", "salary_basis", "salary_period"}),
    )
    session.add(row)
    await session.flush()
    await replace_skills(session, VacancySkill, "vacancy_id", row.id, body.skills)
    await session.commit()
    return offer_record(row)


@router.put(
    "/vacancies/{vacancy_id}", response_model=VacancyOut, summary="Изменить собственную вакансию"
)
async def update_vacancy(
    vacancy_id: UUID,
    body: VacancyInput,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    employer = await employer_for(session, user)
    row = await found(session, Vacancy, vacancy_id, lock=True)
    own_company(row, employer)
    await found(session, Category, body.category_id)
    assign(row, body.model_dump(exclude={"skills", "salary_basis", "salary_period"}))
    row.updated_at = now()
    await replace_skills(session, VacancySkill, "vacancy_id", row.id, body.skills)
    await session.commit()
    return offer_record(row)


@router.post(
    "/vacancies/{vacancy_id}/status",
    response_model=VacancyOut,
    summary="Опубликовать, приостановить или архивировать вакансию",
)
async def vacancy_status(
    vacancy_id: UUID,
    body: VacancyState,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    employer = await employer_for(session, user)
    row = await found(session, Vacancy, vacancy_id, lock=True)
    own_company(row, employer)
    if row.status == enums.VacancyStatus.ARCHIVED and body.status != row.status:
        raise HTTPException(409, "Архивную вакансию нельзя опубликовать повторно")
    row.status = body.status
    if body.status == enums.VacancyStatus.PUBLISHED:
        row.published_at = row.published_at or now()
    row.updated_at = now()
    await session.commit()
    return offer_record(row)


@router.get("/vacancies", response_model=list[VacancyOut], summary="Опубликованные вакансии")
async def vacancies(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    category_id: int | None = None,
    session: AsyncSession = Depends(get_session),
):
    query = select(Vacancy).where(
        Vacancy.status == enums.VacancyStatus.PUBLISHED,
        (Vacancy.expires_at.is_(None)) | (Vacancy.expires_at > now()),
    )
    if category_id:
        query = query.where(Vacancy.category_id == category_id)
    rows = (
        await session.scalars(query.order_by(Vacancy.created_at.desc()).offset(offset).limit(limit))
    ).all()
    return [offer_record(row) for row in rows]


@router.get(
    "/me/vacancies",
    response_model=list[VacancyOut],
    summary="Вакансии собственной компании во всех статусах",
)
async def own_vacancies(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    employer = await employer_for(session, user)
    return [
        offer_record(row)
        for row in (
            await session.scalars(select(Vacancy).where(Vacancy.company_id == employer.company_id))
        ).all()
    ]


@router.get(
    "/vacancies/{vacancy_id}",
    response_model=VacancyOut,
    summary="Опубликованная или собственная вакансия",
)
async def vacancy(
    vacancy_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    row = await found(session, Vacancy, vacancy_id)
    if row.status != enums.VacancyStatus.PUBLISHED:
        own_company(row, await employer_for(session, user))
    return {
        **offer_record(row),
        "skills": [
            record(value)
            for value in (
                await session.scalars(select(VacancySkill).where(VacancySkill.vacancy_id == row.id))
            ).all()
        ],
    }


@router.post(
    "/invitations",
    status_code=201,
    response_model=InvitationOut,
    summary="Пригласить кандидата с предложением и зарплатной вилкой",
)
async def invite(
    body: InvitationInput,
    request: Request,
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", min_length=8, max_length=100
    ),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    employer = await employer_for(session, user)
    operation = None
    if idempotency_key:
        operation = await queue_operation(
            session, user, "invitation_create", idempotency_key, body.model_dump(mode="json")
        )
        if operation.state == "succeeded":
            previous = await found(session, Invitation, operation.resource_id)
            own_company(previous, employer)
            return offer_record(previous)
    candidate = await found(session, Candidate, body.candidate_id)
    if not await searchable(session, candidate, request.app.state.settings):
        raise HTTPException(404, "Профиль недоступен")
    for model, key in ((Vacancy, body.vacancy_id), (EmployerNeed, body.need_id)):
        if key:
            own_company(await found(session, model, key), employer)
    row = Invitation(
        id=uuid4(),
        company_id=employer.company_id,
        sender_user_id=user.id,
        **body.model_dump(exclude={"salary_basis", "salary_period"}),
    )
    session.add(row)
    await session.flush()
    session.add(
        InvitationEvent(
            invitation_id=row.id,
            from_status=None,
            to_status=enums.InvitationStatus.SENT,
            actor_user_id=user.id,
        )
    )
    if operation:
        operation.state = "succeeded"
        operation.resource_id = row.id
        operation.payload = None
    await session.commit()
    return offer_record(row)


async def allowed_invitation(session, invitation_id, user, lock=False):
    row = await found(session, Invitation, invitation_id, lock=lock)
    if row.candidate_id != user.id:
        own_company(row, await employer_for(session, user))
    return row


@router.get(
    "/invitations", response_model=list[InvitationOut], summary="Приглашения своего кабинета"
)
async def invitations(
    cabinet: Literal["candidate", "employer"] = "candidate",
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    if cabinet == "candidate":
        await require_role(session, user, enums.UserRole.CANDIDATE)
        query = select(Invitation).where(Invitation.candidate_id == user.id)
    else:
        employer = await employer_for(session, user)
        query = select(Invitation).where(Invitation.company_id == employer.company_id)
    return [
        offer_record(row)
        for row in (await session.scalars(query.order_by(Invitation.sent_at.desc()))).all()
    ]


@router.get(
    "/invitations/{invitation_id}",
    response_model=InvitationOut,
    summary="Приглашение и история статусов",
)
async def invitation(
    invitation_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    row = await allowed_invitation(session, invitation_id, user)
    company = await found(session, Company, row.company_id)
    events = (
        await session.scalars(
            select(InvitationEvent)
            .where(InvitationEvent.invitation_id == row.id)
            .order_by(InvitationEvent.created_at)
        )
    ).all()
    return {
        **offer_record(row),
        "company_name": company.name,
        "events": [record(value) for value in events],
    }


async def disclose(session, candidate_id, company_id, reason, source_id):
    await session.get(Candidate, candidate_id, with_for_update=True)
    row = await session.scalar(
        select(ContactDisclosure).where(
            ContactDisclosure.candidate_id == candidate_id,
            ContactDisclosure.company_id == company_id,
            ContactDisclosure.revoked_at.is_(None),
        )
    )
    if row is None:
        kwargs = (
            {"invitation_id": source_id}
            if reason == enums.DisclosureReason.INVITATION_ACCEPTED
            else {"application_id": source_id}
        )
        session.add(
            ContactDisclosure(
                candidate_id=candidate_id, company_id=company_id, reason=reason, **kwargs
            )
        )
    elif reason == enums.DisclosureReason.INVITATION_ACCEPTED:
        row.reason = reason
        row.application_id = None
        row.invitation_id = source_id


@router.post(
    "/invitations/{invitation_id}/{action}",
    response_model=InvitationOut,
    summary="Просмотреть, принять, отклонить или отозвать приглашение",
)
async def invitation_action(
    invitation_id: UUID,
    action: Literal["view", "accept", "decline", "withdraw"],
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    row = await allowed_invitation(session, invitation_id, user, lock=True)
    if action != "withdraw" and row.candidate_id != user.id:
        raise HTTPException(403, "Действие доступно кандидату")
    if action == "withdraw":
        own_company(row, await employer_for(session, user))
    target = {
        "view": enums.InvitationStatus.VIEWED,
        "accept": enums.InvitationStatus.ACCEPTED,
        "decline": enums.InvitationStatus.DECLINED,
        "withdraw": enums.InvitationStatus.WITHDRAWN,
    }[action]
    if row.status == target:
        return offer_record(row)
    if row.expires_at and row.expires_at <= now():
        raise HTTPException(409, "Приглашение истекло")
    if row.status not in (enums.InvitationStatus.SENT, enums.InvitationStatus.VIEWED):
        raise HTTPException(409, "Приглашение уже завершено")
    previous, row.status = row.status, target
    if action == "view":
        row.viewed_at = now()
    if action in ("accept", "decline"):
        row.responded_at = now()
    session.add(
        InvitationEvent(
            invitation_id=row.id, from_status=previous, to_status=target, actor_user_id=user.id
        )
    )
    if action == "accept":
        await disclose(
            session,
            row.candidate_id,
            row.company_id,
            enums.DisclosureReason.INVITATION_ACCEPTED,
            row.id,
        )
    await session.commit()
    return offer_record(row)


@router.post(
    "/vacancies/{vacancy_id}/applications",
    status_code=201,
    response_model=ApplicationOut,
    summary="Откликнуться самостоятельно",
)
async def apply(
    vacancy_id: UUID,
    body: ApplyInput,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await require_role(session, user, enums.UserRole.CANDIDATE)
    await found(session, Candidate, user.id, lock=True)
    vacancy = await found(session, Vacancy, vacancy_id)
    if vacancy.status != enums.VacancyStatus.PUBLISHED or (
        vacancy.expires_at and vacancy.expires_at <= now()
    ):
        raise HTTPException(409, "Вакансия не опубликована или истекла")
    row = await session.scalar(
        select(Application).where(
            Application.vacancy_id == vacancy_id, Application.candidate_id == user.id
        )
    )
    if row:
        return record(row)
    row = Application(id=uuid4(), vacancy_id=vacancy_id, candidate_id=user.id, **body.model_dump())
    session.add(row)
    await session.flush()
    session.add(
        ApplicationEvent(
            application_id=row.id,
            from_status=None,
            to_status=enums.ApplicationStatus.SENT,
            actor_user_id=user.id,
        )
    )
    await disclose(
        session, user.id, vacancy.company_id, enums.DisclosureReason.APPLICATION_SENT, row.id
    )
    await session.commit()
    return record(row)


@router.get(
    "/applications",
    response_model=list[ApplicationOut],
    summary="Свои отклики или отклики на вакансии компании",
)
async def applications(
    cabinet: Literal["candidate", "employer"] = "candidate",
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    query = select(Application)
    if cabinet == "candidate":
        await require_role(session, user, enums.UserRole.CANDIDATE)
        query = query.where(Application.candidate_id == user.id)
    else:
        employer = await employer_for(session, user)
        query = query.join(Vacancy).where(Vacancy.company_id == employer.company_id)
    return [
        record(row)
        for row in (await session.scalars(query.order_by(Application.created_at.desc()))).all()
    ]


@router.post(
    "/applications/{application_id}/{action}",
    response_model=ApplicationOut,
    summary="Изменить статус собственного отклика",
)
async def application_action(
    application_id: UUID,
    action: Literal["view", "invite", "reject", "withdraw"],
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    row = await found(session, Application, application_id, lock=True)
    vacancy = await found(session, Vacancy, row.vacancy_id)
    if action == "withdraw":
        if row.candidate_id != user.id:
            raise HTTPException(404, "Отклик не найден")
    else:
        own_company(vacancy, await employer_for(session, user))
    target = {
        "view": enums.ApplicationStatus.VIEWED,
        "invite": enums.ApplicationStatus.INVITED,
        "reject": enums.ApplicationStatus.REJECTED,
        "withdraw": enums.ApplicationStatus.WITHDRAWN,
    }[action]
    if row.status == target:
        return record(row)
    if row.status in (enums.ApplicationStatus.REJECTED, enums.ApplicationStatus.WITHDRAWN):
        raise HTTPException(409, "Отклик уже завершён")
    session.add(
        ApplicationEvent(
            application_id=row.id, from_status=row.status, to_status=target, actor_user_id=user.id
        )
    )
    row.status = target
    row.responded_at = now()
    if action == "withdraw":
        disclosure = await session.scalar(
            select(ContactDisclosure).where(
                ContactDisclosure.application_id == row.id, ContactDisclosure.revoked_at.is_(None)
            )
        )
        if disclosure:
            disclosure.revoked_at = now()
    await session.commit()
    return record(row)


@router.post("/me/disclosures/{company_id}/revoke", summary="Отозвать доступ компании к контактам")
async def revoke(
    company_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await found(session, Candidate, user.id, lock=True)
    rows = (
        await session.scalars(
            select(ContactDisclosure).where(
                ContactDisclosure.candidate_id == user.id,
                ContactDisclosure.company_id == company_id,
                ContactDisclosure.revoked_at.is_(None),
            )
        )
    ).all()
    for row in rows:
        row.revoked_at = now()
    await session.commit()
    return {"status": "done"}
