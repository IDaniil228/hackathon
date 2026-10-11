from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common import Input, found, record
from app.db import enums
from app.db.models.assess import RegularAssignment, Task, TaskTemplate
from app.db.models.backend import EmployerTask
from app.db.models.core import User
from app.db.models.profile import Candidate
from app.db.models.ref import Category
from app.db.session import get_session
from app.security import current_user, employer_for, now
from app.visibility import searchable

router = APIRouter(tags=["Короткие задания работодателя"])


class AssignmentCreate(Input):
    candidate_id: UUID
    title: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=1, max_length=20000)
    due_at: datetime | None = None
    max_score: float = Field(default=10, gt=0, le=100)


class AssignmentAnswer(Input):
    answer: str = Field(min_length=1, max_length=20000)


class AssignmentScore(Input):
    score: float = Field(ge=0)
    feedback: str = Field(min_length=1, max_length=4000)


async def owned_assignment(session, user, assignment_id, lock=False):
    row = await found(session, RegularAssignment, assignment_id, lock=lock)
    if row.candidate_id != user.id:
        employer = await employer_for(session, user)
        if row.company_id != employer.company_id:
            raise HTTPException(404, "Задание не найдено")
    return row


@router.post("/assignments", status_code=201, summary="Назначить короткое ручное задание кандидату")
async def create(
    body: AssignmentCreate,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    employer = await employer_for(session, user)
    candidate = await found(session, Candidate, body.candidate_id)
    if not await searchable(session, candidate, request.app.state.settings):
        raise HTTPException(404, "Кандидат недоступен")
    if not candidate.current_category_id:
        raise HTTPException(409, "Кандидат ещё не выбрал категорию")
    category = await found(session, Category, candidate.current_category_id)
    if body.due_at and (body.due_at.tzinfo is None or body.due_at <= now()):
        raise HTTPException(422, "Срок должен быть будущим моментом с часовым поясом")
    template = TaskTemplate(
        specialization_id=category.specialization_id,
        grade_id=category.grade_id,
        code="employer_" + uuid4().hex,
        title=body.title,
        format=enums.QuestionFormat.OPEN,
        difficulty=1,
        max_score=body.max_score,
        source=enums.TemplateSource.MANUAL,
        is_active=False,
    )
    session.add(template)
    await session.flush()
    session.add(EmployerTask(template_id=template.id, company_id=employer.company_id))
    task = Task(
        id=uuid4(),
        template_id=template.id,
        candidate_id=candidate.user_id,
        statement=body.statement,
        difficulty=1,
        max_score=body.max_score,
    )
    session.add(task)
    await session.flush()
    row = RegularAssignment(
        id=uuid4(),
        candidate_id=candidate.user_id,
        task_id=task.id,
        company_id=employer.company_id,
        due_at=body.due_at,
        max_score=body.max_score,
    )
    session.add(row)
    await session.commit()
    return record(row)


@router.get("/assignments", summary="Задания своего кабинета")
async def assignments(
    cabinet: Literal["candidate", "employer"] = "candidate",
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    query = (
        select(RegularAssignment, TaskTemplate.title)
        .join(Task, RegularAssignment.task_id == Task.id)
        .join(TaskTemplate, Task.template_id == TaskTemplate.id)
    )
    if cabinet == "candidate":
        query = query.where(RegularAssignment.candidate_id == user.id)
    else:
        employer = await employer_for(session, user)
        query = query.where(RegularAssignment.company_id == employer.company_id)
    return [
        {**record(row), "title": title}
        for row, title in (
            await session.execute(query.order_by(RegularAssignment.issued_at.desc()))
        ).all()
    ]


@router.get("/assignments/{assignment_id}", summary="Условие собственного задания и обратная связь")
async def assignment(
    assignment_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    row = await owned_assignment(session, user, assignment_id)
    task = await found(session, Task, row.task_id)
    owner = await found(session, EmployerTask, task.template_id)
    template = await found(session, TaskTemplate, task.template_id)
    return {
        **record(row),
        "title": template.title,
        "statement": task.statement,
        "feedback": owner.feedback,
    }


@router.post(
    "/assignments/{assignment_id}/answer", summary="Ответить на короткое задание работодателя"
)
async def answer(
    assignment_id: UUID,
    body: AssignmentAnswer,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    row = await owned_assignment(session, user, assignment_id, lock=True)
    if row.candidate_id != user.id:
        raise HTTPException(403, "Ответить может только кандидат")
    if row.status == enums.AssignmentStatus.SCORED or (row.due_at and row.due_at <= now()):
        raise HTTPException(409, "Задание уже оценено или истекло")
    row.answer_text, row.answered_at, row.status = (
        body.answer,
        now(),
        enums.AssignmentStatus.ANSWERED,
    )
    await session.commit()
    return record(row)


@router.post(
    "/assignments/{assignment_id}/score", summary="Оценить короткое задание без изменения грейда"
)
async def score(
    assignment_id: UUID,
    body: AssignmentScore,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    row = await owned_assignment(session, user, assignment_id, lock=True)
    employer = await employer_for(session, user)
    if employer.company_id != row.company_id:
        raise HTTPException(404, "Задание не найдено")
    if row.status != enums.AssignmentStatus.ANSWERED:
        raise HTTPException(409, "Задание ещё не отвечено или уже оценено")
    if body.score > row.max_score:
        raise HTTPException(422, "Балл превышает максимум")
    task = await found(session, Task, row.task_id)
    owner = await found(session, EmployerTask, task.template_id)
    owner.feedback = body.feedback
    row.score, row.status = body.score, enums.AssignmentStatus.SCORED
    candidate = await found(session, Candidate, row.candidate_id, lock=True)
    candidate.profile_refreshed_at = now()
    await session.commit()
    return record(row)
