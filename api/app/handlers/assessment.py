from datetime import timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common import Input, found, record
from app.db import enums
from app.db.models.assess import (
    GradeAssignment,
    SurveyAnswer,
    SurveyOption,
    SurveyQuestion,
    SurveySession,
    SurveyTemplate,
    TestSession,
)
from app.db.models.backend import ServiceOperation, TestBridge
from app.db.models.core import User
from app.db.models.profile import CandidateSkill
from app.db.models.ref import Category, Grade, Skill, Specialization
from app.db.session import get_session
from app.handlers.matching import IdempotencyKey
from app.handlers.profiles import candidate_for
from app.operations import call_service, operation_out, queue_operation, require_service
from app.schemas import CompletedTest, OperationOut, TestOut, TestResultOut
from app.security import current_user, now

router = APIRouter(tags=["Опрос и тестирование"])
AREA_MAP = {"backend": "backend", "ml_engineer": "ml"}
SKILL_MAP = {"rest_api": "http-api"}


class SurveyValue(Input):
    question_id: int
    option_ids: list[int] = Field(default=[], max_length=100)
    free_text: str | None = Field(default=None, max_length=1000)


class SurveySubmit(Input):
    specialization_id: int = Field(gt=0)
    claimed_grade_id: int = Field(gt=0)
    answers: list[SurveyValue] = Field(max_length=100)


class TestCreate(Input):
    survey_session_id: UUID
    skill_ids: list[int] | None = Field(
        default=None,
        max_length=100,
        description="Выбранные заявленные навыки для теста; null — все навыки профиля, [] — тест направления без предпочтения навыков",
    )


class Answer(Input):
    content: str | None = Field(default=None, min_length=1, max_length=200000)
    choice: str | None = Field(default=None, min_length=1, max_length=16)
    language: str | None = Field(default=None, max_length=40)
    paste_count: int | None = Field(default=None, ge=0, le=32767)
    focus_lost_count: int | None = Field(default=None, ge=0, le=32767)
    time_spent_sec: float | None = Field(default=None, ge=0)


@router.get("/surveys", summary="Доступные входные опросы")
async def surveys(session: AsyncSession = Depends(get_session)):
    return [
        record(row)
        for row in (
            await session.scalars(select(SurveyTemplate).where(SurveyTemplate.is_active))
        ).all()
    ]


@router.get("/surveys/{survey_id}", summary="Вопросы и варианты входного опроса")
async def survey(survey_id: int, session: AsyncSession = Depends(get_session)):
    template = await found(session, SurveyTemplate, survey_id)
    if not template.is_active:
        raise HTTPException(404, "Опрос недоступен")
    questions = (
        await session.scalars(
            select(SurveyQuestion)
            .where(SurveyQuestion.survey_id == survey_id)
            .order_by(SurveyQuestion.order_no)
        )
    ).all()
    result = []
    for question in questions:
        options = (
            await session.scalars(
                select(SurveyOption)
                .where(SurveyOption.question_id == question.id)
                .order_by(SurveyOption.order_no)
            )
        ).all()
        result.append({**record(question), "options": [record(item) for item in options]})
    return {**record(template), "questions": result}


@router.post(
    "/surveys/{survey_id}/submit",
    status_code=201,
    summary="Пройти опрос и самостоятельно выбрать грейд",
)
async def submit_survey(
    survey_id: int,
    body: SurveySubmit,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    candidate = await candidate_for(session, user)
    await session.refresh(candidate, with_for_update=True)
    template = await found(session, SurveyTemplate, survey_id)
    specialization = await found(session, Specialization, body.specialization_id)
    grade = await found(session, Grade, body.claimed_grade_id)
    if (
        not template.is_active
        or specialization.industry_id != template.industry_id
        or grade.industry_id != template.industry_id
    ):
        raise HTTPException(422, "Опрос, специализация и грейд должны относиться к одной отрасли")
    questions = (
        await session.scalars(select(SurveyQuestion).where(SurveyQuestion.survey_id == survey_id))
    ).all()
    supplied = {value.question_id: value for value in body.answers}
    if len(supplied) != len(body.answers) or set(supplied) - {item.id for item in questions}:
        raise HTTPException(422, "Повторный или чужой вопрос")
    row = SurveySession(
        id=uuid4(),
        candidate_id=user.id,
        survey_id=survey_id,
        status=enums.SessionStatus.SCORED,
        specialization_id=specialization.id,
        claimed_grade_id=grade.id,
        finished_at=now(),
    )
    session.add(row)
    await session.flush()
    for question in questions:
        value = supplied.get(question.id)
        if value is None or (not value.option_ids and not value.free_text):
            if question.is_required:
                raise HTTPException(422, "Не все обязательные вопросы заполнены")
            continue
        if question.format == enums.QuestionFormat.SINGLE_CHOICE and len(value.option_ids) != 1:
            raise HTTPException(422, "Нужен один вариант ответа")
        if question.format in (
            enums.QuestionFormat.SINGLE_CHOICE,
            enums.QuestionFormat.MULTI_CHOICE,
        ) and (value.free_text or not value.option_ids):
            raise HTTPException(422, "Ожидаются варианты ответа")
        if question.format not in (
            enums.QuestionFormat.SINGLE_CHOICE,
            enums.QuestionFormat.MULTI_CHOICE,
        ) and (value.option_ids or not value.free_text):
            raise HTTPException(422, "Ожидается текстовый ответ")
        if len(set(value.option_ids)) != len(value.option_ids):
            raise HTTPException(422, "Варианты не должны повторяться")
        for option_id in value.option_ids:
            option = await found(session, SurveyOption, option_id)
            if option.question_id != question.id:
                raise HTTPException(422, "Вариант относится к другому вопросу")
            session.add(
                SurveyAnswer(session_id=row.id, question_id=question.id, option_id=option_id)
            )
        if value.free_text:
            session.add(
                SurveyAnswer(session_id=row.id, question_id=question.id, free_text=value.free_text)
            )
    current = await session.scalar(
        select(GradeAssignment).where(
            GradeAssignment.candidate_id == user.id, GradeAssignment.is_current
        )
    )
    if not current or current.source != enums.GradeSource.TEST:
        category = await session.scalar(
            select(Category).where(
                Category.specialization_id == specialization.id, Category.grade_id == grade.id
            )
        )
        if category is None:
            raise HTTPException(422, "Категория отсутствует в справочнике")
        if current:
            current.is_current = False
            await session.flush()
        session.add(
            GradeAssignment(
                candidate_id=user.id,
                category_id=category.id,
                source=enums.GradeSource.MANUAL,
                next_change_allowed_at=now(),
                note="Самостоятельный выбор, тест не подтверждён",
            )
        )
        candidate.current_category_id = category.id
    await session.commit()
    return record(row)


@router.get("/me/surveys", summary="Собственная история опросов")
async def survey_history(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    await candidate_for(session, user)
    return [
        record(row)
        for row in (
            await session.scalars(
                select(SurveySession).where(SurveySession.candidate_id == user.id)
            )
        ).all()
    ]


@router.get(
    "/test-capabilities", summary="Покрытие банка тестов без подмены неподдерживаемых направлений"
)
async def capabilities(request: Request):
    templates = await call_service(request.app, "taskgen", "GET", "/templates")
    coverage = {}
    for template in templates:
        key = (template.get("area"), template.get("grade"))
        coverage.setdefault(key, set()).add(template.get("kind"))
    return {
        "mapping": AREA_MAP,
        "skill_mapping": SKILL_MAP,
        "skills": {
            area: sorted(
                {
                    skill
                    for template in templates
                    if template.get("area") == area
                    for skill in template.get("skills", [])
                }
            )
            for area in AREA_MAP.values()
        },
        "coverage": [
            {
                "specialization": code,
                "area": area,
                "grade": grade,
                "kinds": sorted(coverage.get((area, grade), set())),
                "has_templates": bool(coverage.get((area, grade))),
            }
            for code, area in AREA_MAP.items()
            for grade in ("junior", "middle", "senior")
        ],
        "note": "Наличие шаблонов не гарантирует квоты конфигурации; taskgen проверяет их при создании сессии",
    }


@router.post(
    "/tests",
    status_code=202,
    response_model=OperationOut,
    summary="Создать собственную тестовую попытку через taskgen",
)
async def create_test(
    body: TestCreate,
    request: Request,
    idempotency_key: IdempotencyKey,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    require_service(request.app, "taskgen")
    candidate = await candidate_for(session, user)
    await session.refresh(candidate, with_for_update=True)
    survey = await found(session, SurveySession, body.survey_session_id)
    if survey.candidate_id != user.id or survey.status != enums.SessionStatus.SCORED:
        raise HTTPException(404, "Завершённый опрос не найден")
    previous = await session.scalar(
        select(ServiceOperation).where(
            ServiceOperation.owner_id == user.id,
            ServiceOperation.kind == "test_create",
            ServiceOperation.key == idempotency_key,
        )
    )
    if previous:
        previous_test = await session.get(TestSession, previous.resource_id)
        if previous_test.survey_session_id != survey.id or (previous.response or {}).get(
            "request"
        ) != body.model_dump(mode="json"):
            raise HTTPException(409, "Ключ использован для другого опроса")
        return operation_out(previous)
    specialization = await found(session, Specialization, survey.specialization_id)
    grade = await found(session, Grade, survey.claimed_grade_id)
    area = AREA_MAP.get(specialization.code)
    if area is None or grade.code not in ("junior", "middle", "senior"):
        raise HTTPException(409, "Для этой специализации или грейда банк тестов пока не подключён")
    open_rows = (
        await session.scalars(
            select(TestSession).where(
                TestSession.candidate_id == user.id,
                TestSession.status.in_(["created", "in_progress", "submitted"]),
            )
        )
    ).all()
    for local in open_rows:
        if local.deadline_at and local.deadline_at <= now():
            local.status = enums.SessionStatus.EXPIRED
        else:
            raise HTTPException(409, "Уже есть незавершённая попытка")
    assignment = await session.scalar(
        select(GradeAssignment).where(
            GradeAssignment.candidate_id == user.id, GradeAssignment.is_current
        )
    )
    if (
        assignment
        and assignment.source == enums.GradeSource.TEST
        and assignment.next_change_allowed_at > now()
    ):
        raise HTTPException(
            409, {"code": "grade_cooldown", "until": assignment.next_change_allowed_at.isoformat()}
        )
    failed = await session.scalar(
        select(TestSession)
        .where(
            TestSession.candidate_id == user.id,
            TestSession.specialization_id == specialization.id,
            TestSession.claimed_grade_id == grade.id,
            TestSession.outcome == enums.SessionOutcome.FAILED,
        )
        .order_by(TestSession.finished_at.desc())
        .limit(1)
    )
    if failed and failed.finished_at + timedelta(days=1) > now():
        raise HTTPException(409, "Повтор этого проваленного уровня доступен через сутки")
    owned_skills = (
        await session.execute(
            select(Skill.id, Skill.code)
            .join(CandidateSkill)
            .where(CandidateSkill.candidate_id == user.id)
        )
    ).all()
    if body.skill_ids is not None and set(body.skill_ids) - {item.id for item in owned_skills}:
        raise HTTPException(422, "Для теста можно выбрать только заявленные навыки профиля")
    skills = [
        item.code for item in owned_skills if body.skill_ids is None or item.id in body.skill_ids
    ]
    local = TestSession(
        id=uuid4(),
        candidate_id=user.id,
        survey_session_id=survey.id,
        specialization_id=specialization.id,
        claimed_grade_id=grade.id,
        deadline_at=now() + timedelta(hours=24),
    )
    session.add(local)
    await session.flush()
    session.add(TestBridge(session_id=local.id, area=area, grade=grade.code))
    body_remote = {
        "candidate_ref": str(user.id),
        "area": area,
        "claimed_grade": grade.code,
        "skills": [SKILL_MAP.get(code, code) for code in skills],
        "early_start": False,
    }
    operation = await queue_operation(
        session,
        user,
        "test_create",
        idempotency_key,
        {
            "service": "taskgen",
            "path": "/sessions",
            "body": body_remote,
            "survey_id": str(survey.id),
        },
        local.id,
    )
    operation.response = {"request": body.model_dump(mode="json")}
    await session.commit()
    return operation_out(operation)


async def own_test(session, user, test_id):
    local = await found(session, TestSession, test_id)
    if local.candidate_id != user.id:
        raise HTTPException(404, "Попытка не найдена")
    return local, await found(session, TestBridge, test_id)


async def remote_test(request, session, user, test_id):
    local, bridge = await own_test(session, user, test_id)
    if bridge.remote_id is None:
        raise HTTPException(409, {"code": "session_not_created", "state": bridge.state})
    remote_id, area, grade = bridge.remote_id, bridge.area, bridge.grade
    await session.commit()
    remote = await call_service(request.app, "taskgen", "GET", f"/sessions/{remote_id}")
    if (
        remote.get("candidate_ref") != str(user.id)
        or remote.get("area") != area
        or remote.get("claimed_grade") != grade
    ):
        raise HTTPException(502, "Сервис вернул сессию другого кандидата или категории")
    return remote


@router.get(
    "/tests", response_model=list[TestOut], summary="Собственные попытки и состояния интеграции"
)
async def tests(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    await candidate_for(session, user)
    rows = (
        await session.execute(
            select(TestSession, TestBridge)
            .join(TestBridge)
            .where(TestSession.candidate_id == user.id)
            .order_by(TestSession.created_at.desc())
        )
    ).all()
    return [
        {
            **record(local),
            "service_state": bridge.state,
            "result_applied": bridge.applied_at is not None,
        }
        for local, bridge in rows
    ]


@router.get(
    "/tests/{test_id}",
    response_model=TestOut,
    summary="Статус и публичные задания собственной попытки",
)
async def test(
    test_id: UUID,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    local, bridge = await own_test(session, user, test_id)
    local_data = record(local)
    if bridge.remote_id is None or local.status in (
        enums.SessionStatus.CANCELLED,
        enums.SessionStatus.EXPIRED,
    ):
        return {**local_data, "service_state": bridge.state, "tasks": []}
    remote = await remote_test(request, session, user, test_id)
    local, bridge = await own_test(session, user, test_id)
    await session.refresh(local, with_for_update=True)
    await session.refresh(bridge, with_for_update=True)
    if local.status in (enums.SessionStatus.CANCELLED, enums.SessionStatus.EXPIRED):
        return {**record(local), "service_state": bridge.state, "tasks": []}
    if not bridge.applied_at:
        bridge.state = remote["status"]
    if bridge.state == "failed" and local.status != enums.SessionStatus.SCORED:
        local.status = enums.SessionStatus.CANCELLED
    if bridge.state == "ready" and local.status == enums.SessionStatus.CREATED:
        local.status = enums.SessionStatus.IN_PROGRESS
        local.started_at = now()
    await session.commit()
    tasks = [
        {
            key: item.get(key)
            for key in ("id", "position", "kind", "status", "statement", "public", "failure")
        }
        for item in remote.get("tasks", [])
    ]
    return {
        **record(local),
        "service_state": bridge.state,
        "tasks_issued": remote.get("tasks_issued", False),
        "failure": remote.get("failure"),
        "tasks": tasks,
    }


@router.post(
    "/tests/{test_id}/tasks/{task_id}/answer", summary="Ответить на задание собственной попытки"
)
async def answer(
    test_id: UUID,
    task_id: UUID,
    body: Answer,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    local, bridge = await own_test(session, user, test_id)
    if (
        local.status not in (enums.SessionStatus.CREATED, enums.SessionStatus.IN_PROGRESS)
        or local.deadline_at <= now()
    ):
        raise HTTPException(409, "Попытка закрыта")
    remote = await remote_test(request, session, user, test_id)
    if str(task_id) not in {item["id"] for item in remote.get("tasks", [])}:
        raise HTTPException(404, "Задание не принадлежит попытке")
    return await call_service(
        request.app,
        "taskgen",
        "POST",
        f"/tasks/{task_id}/answer",
        json=body.model_dump(exclude_none=True),
    )


@router.post(
    "/tests/{test_id}/finish",
    status_code=202,
    response_model=OperationOut | CompletedTest,
    summary="Завершить попытку и применить результат ровно один раз",
)
async def finish(
    test_id: UUID,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    local, bridge = await own_test(session, user, test_id)
    await session.refresh(local, with_for_update=True)
    if bridge.applied_at:
        return {"status": "done", "result": bridge.result}
    if (
        local.status in (enums.SessionStatus.CANCELLED, enums.SessionStatus.EXPIRED)
        or bridge.remote_id is None
    ):
        raise HTTPException(409, "Попытка закрыта или не создана")
    if local.deadline_at <= now():
        raise HTTPException(409, "Попытка истекла")
    remote = await remote_test(request, session, user, test_id)
    if remote.get("status") not in ("ready", "scored"):
        raise HTTPException(409, "Набор заданий ещё не готов")
    local, bridge = await own_test(session, user, test_id)
    await session.refresh(local, with_for_update=True)
    await session.refresh(bridge, with_for_update=True)
    if local.status in (enums.SessionStatus.CANCELLED, enums.SessionStatus.EXPIRED):
        raise HTTPException(409, "Попытка закрыта")
    if bridge.applied_at:
        return {"status": "done", "result": bridge.result}
    row = await queue_operation(session, user, "test_score", f"finish-{test_id}", {}, local.id)
    row.remote_id = bridge.remote_id
    local.status = enums.SessionStatus.SUBMITTED
    await session.commit()
    return operation_out(row)


@router.get(
    "/tests/{test_id}/result",
    response_model=TestResultOut,
    summary="Сохранённый результат собственной попытки",
)
async def result(
    test_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    local, bridge = await own_test(session, user, test_id)
    if bridge.result is None:
        raise HTTPException(409, "Результат ещё не получен")
    return {
        "result": bridge.result,
        "outcome": local.outcome,
        "assigned_category_id": local.assigned_category_id,
        "applied_at": bridge.applied_at,
        "service_state": bridge.state,
    }


@router.post("/tests/{test_id}/cancel", summary="Отменить собственную попытку без изменения грейда")
async def cancel(
    test_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    local, bridge = await own_test(session, user, test_id)
    await session.refresh(local, with_for_update=True)
    if local.status == enums.SessionStatus.SCORED:
        raise HTTPException(409, "Оценённую попытку нельзя отменить")
    local.status = enums.SessionStatus.CANCELLED
    await session.commit()
    return record(local)


@router.get("/me/grades", summary="Собственная история присвоения категорий")
async def grades(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return [
        record(row)
        for row in (
            await session.scalars(
                select(GradeAssignment)
                .where(GradeAssignment.candidate_id == user.id)
                .order_by(GradeAssignment.assigned_at.desc())
            )
        ).all()
    ]
