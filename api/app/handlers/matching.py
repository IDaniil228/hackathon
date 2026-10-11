from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common import Input, found, own_company, record
from app.db.models.backend import ServiceOperation
from app.db.models.core import User
from app.db.models.hire import EmployerNeed
from app.db.models.matching import SearchResult, SearchRun
from app.db.session import get_session
from app.operations import call_service, operation_out, queue_operation, require_service
from app.schemas import MapOut, MatchResults, OperationOut, RunOut
from app.security import current_user, employer_for
from app.visibility import card, safe_match_result

router = APIRouter(tags=["Подбор"])
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=100)]


class Refine(Input):
    message: str = Field(min_length=1, max_length=20000)


async def own_run(session, user, run_id):
    employer = await employer_for(session, user)
    run = await found(session, SearchRun, run_id)
    own_company(await found(session, EmployerNeed, run.need_id), employer)
    return run, employer


@router.get(
    "/operations/{operation_id}",
    response_model=OperationOut,
    summary="Состояние сохраняемой сервисной операции",
)
async def operation(
    operation_id: UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    row = await found(session, ServiceOperation, operation_id)
    if row.owner_id != user.id:
        raise HTTPException(404, "Операция не найдена")
    return operation_out(row)


@router.post(
    "/needs/{need_id}/runs",
    status_code=202,
    response_model=OperationOut,
    summary="Запустить подбор через matcher",
)
async def start_run(
    need_id: UUID,
    request: Request,
    idempotency_key: IdempotencyKey,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    require_service(request.app, "matcher")
    employer = await employer_for(session, user)
    need = await found(session, EmployerNeed, need_id)
    own_company(need, employer)
    if not need.is_active:
        raise HTTPException(409, "Потребность закрыта")
    row = await queue_operation(
        session,
        user,
        "match_create",
        idempotency_key,
        {
            "service": "matcher",
            "path": f"/needs/{need_id}/runs",
            "body": {"author_user_id": str(user.id)},
        },
        need_id,
    )
    await session.commit()
    return operation_out(row)


@router.post(
    "/runs/{run_id}/refine",
    status_code=202,
    response_model=OperationOut,
    summary="Уточнить подбор и сохранить предыдущий прогон",
)
async def refine(
    run_id: UUID,
    body: Refine,
    request: Request,
    idempotency_key: IdempotencyKey,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    require_service(request.app, "matcher")
    run, _ = await own_run(session, user, run_id)
    row = await queue_operation(
        session,
        user,
        "match_refine",
        idempotency_key,
        {
            "service": "matcher",
            "path": f"/runs/{run_id}/refine",
            "body": {"message": body.message, "author_user_id": str(user.id)},
        },
        run_id,
    )
    await session.commit()
    return operation_out(row)


@router.get(
    "/needs/{need_id}/runs",
    response_model=list[RunOut],
    summary="История прогонов собственной потребности",
)
async def history(
    need_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    employer = await employer_for(session, user)
    own_company(await found(session, EmployerNeed, need_id), employer)
    rows = (
        await session.scalars(
            select(SearchRun)
            .where(SearchRun.need_id == need_id)
            .order_by(SearchRun.created_at.desc())
        )
    ).all()
    return [record(row, ["summary", "error_message", "params"]) for row in rows]


@router.get(
    "/runs/{run_id}",
    response_model=RunOut,
    summary="Статус собственного прогона без исторических персональных текстов",
)
async def run_status(
    run_id: UUID, user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    run, _ = await own_run(session, user, run_id)
    return record(run, ["summary", "error_message", "params"])


@router.get(
    "/runs/{run_id}/results",
    response_model=MatchResults,
    response_model_exclude_unset=True,
    summary="Готовая выдача с текущей приватностью кандидатов",
)
async def results(
    run_id: UUID,
    request: Request,
    city: str | None = None,
    relocation: bool | None = None,
    work_format: Literal["office", "hybrid", "remote"] | None = None,
    grade_confirmed: bool | None = None,
    skills: Annotated[list[str], Query()] = (),
    salary_max: int | None = Query(default=None, ge=0),
    min_score: float | None = Query(default=None, ge=0, le=1),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    run, employer = await own_run(session, user, run_id)
    company_id = employer.company_id
    if str(run.status) != "done":
        raise HTTPException(409, "Прогон ещё не завершён")
    params = [
        (name, str(value).lower() if isinstance(value, bool) else str(value))
        for name, value in {
            "city": city,
            "relocation": relocation,
            "work_format": work_format,
            "grade_confirmed": grade_confirmed,
            "salary_max": salary_max,
            "min_score": min_score,
        }.items()
        if value is not None
    ]
    params.extend(("skills", skill) for skill in skills)
    await session.commit()
    upstream = await call_service(
        request.app, "matcher", "GET", f"/runs/{run_id}/results", params=params
    )
    visible = []
    for row in upstream["results"]:
        try:
            value = await card(
                session, UUID(row["candidate_id"]), request.app.state.settings, company_id
            )
        except HTTPException as error:
            if error.status_code != 404:
                raise
            continue
        if (
            city is not None or relocation is not None or work_format is not None
        ) and "city" not in value:
            continue
        if salary_max is not None and "preferences" not in value:
            continue
        visible.append(safe_match_result(row, value))
    all_ids = (
        await session.scalars(
            select(SearchResult.candidate_id).where(
                SearchResult.run_id == run_id, SearchResult.is_shown, SearchResult.rank.is_not(None)
            )
        )
    ).all()
    total = 0
    for candidate_id in all_ids:
        try:
            await card(session, candidate_id, request.app.state.settings, company_id)
            total += 1
        except HTTPException as error:
            if error.status_code != 404:
                raise
    return {
        "total": total,
        "matched": len(visible),
        "results": visible,
        "summary": "Выдача учитывает текущую видимость профилей. Оценки относятся к моменту прогона",
    }


@router.get(
    "/runs/{run_id}/map",
    response_model=MapOut,
    summary="Карта рассматривавшихся кандидатов с текущими правами",
)
async def run_map(
    run_id: UUID,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    _, employer = await own_run(session, user, run_id)
    company_id = employer.company_id
    await session.commit()
    manifest = await call_service(request.app, "matcher", "GET", f"/runs/{run_id}/map-members")
    cards = {}
    for member in manifest["members"]:
        try:
            value = await card(
                session, UUID(member["candidate_id"]), request.app.state.settings, company_id
            )
        except HTTPException as error:
            if error.status_code != 404:
                raise
            continue
        allowed = True
        for field in member["requires"]:
            if field == "private_preferences":
                allowed = False
            elif field == "github":
                allowed &= any(link["kind"] == "github" for link in value.get("links", []))
            else:
                allowed &= field in value
        if allowed:
            cards[member["candidate_id"]] = value
    await session.commit()
    result = await call_service(
        request.app, "matcher", "POST", f"/runs/{run_id}/map", json={"candidate_ids": list(cards)}
    )
    points = []
    for point in result["points"]:
        value = await card(
            session, UUID(point["candidate_id"]), request.app.state.settings, company_id
        )
        if value != cards[point["candidate_id"]]:
            raise HTTPException(409, "Права или профиль изменились; обновите карту")
        points.append({**point, "candidate": value})
    from fastapi.responses import JSONResponse

    return JSONResponse(
        MapOut(**{**result, "points": points}).model_dump(mode="json", exclude_unset=True),
        headers={"Cache-Control": "private, no-store"},
    )
