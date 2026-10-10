import asyncio
import re
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common import Input, assign, found, record
from app.db import enums
from app.db.models.core import User
from app.db.models.profile import CandidateSkill, ResumeDocument, ResumeParseResult
from app.db.models.ref import Skill, SkillAlias
from app.db.session import get_session
from app.documents import extract_text, render_pdf
from app.handlers.profiles import ProfileUpdate, candidate_for
from app.resume_parser import propose_resume
from app.security import current_user, employer_for, now
from app.visibility import card, has_consent

router = APIRouter(tags=["Резюме и PDF"])


class ApplyParse(Input):
    accept_fields: list[str]
    accept_skill_ids: list[int] = []


@router.post(
    "/me/resumes", status_code=201, summary="Загрузить PDF и получить предложения заполнения анкеты"
)
async def upload(
    request: Request,
    file: UploadFile,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await candidate_for(session, user)
    if not await has_consent(session, user.id, enums.ConsentKind.PD_PROCESSING):
        raise HTTPException(403, "Загрузка требует действующего согласия на обработку данных")
    data = bytearray()
    try:
        while chunk := await file.read(65536):
            data.extend(chunk)
            if len(data) > 20971520:
                raise HTTPException(413, "Размер PDF превышает 20 MiB")
    finally:
        await file.close()
    if not data.startswith(b"%PDF-"):
        raise HTTPException(415, "Ожидается PDF")
    await session.commit()
    try:
        raw = await asyncio.to_thread(extract_text, bytes(data))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    phone = re.search(r"(?<!\w)\+?[0-9][0-9 ()-]{8,20}[0-9](?!\w)", raw)
    proposal = {}
    if phone:
        normalized = re.sub(r"[^0-9+]", "", phone.group())
        if re.fullmatch(r"\+?[0-9]{7,15}", normalized):
            proposal["phone"] = normalized
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if lines and re.fullmatch(r"[А-ЯЁ][а-яё]+ [А-ЯЁ][а-яё]+(?: [А-ЯЁ][а-яё]+)?", lines[0]):
        parts = lines[0].split()
        proposal.update(last_name=parts[0], first_name=parts[1])
        if len(parts) == 3:
            proposal["middle_name"] = parts[2]
    skills = (await session.scalars(select(Skill))).all()
    aliases = (await session.scalars(select(SkillAlias))).all()
    detected = {
        skill.id
        for skill in skills
        if re.search(r"(?<!\w)" + re.escape(skill.name) + r"(?!\w)", raw, re.I)
    }
    detected.update(
        alias.skill_id
        for alias in aliases
        if re.search(r"(?<!\w)" + re.escape(alias.alias) + r"(?!\w)", raw, re.I)
    )
    llm = request.app.state.resume_llm
    llm_proposal = None
    if llm is not None:
        await session.commit()
        llm_proposal = await propose_resume(
            llm, request.app.state.settings.llm, raw, {skill.code: skill.name for skill in skills}
        )
        if llm_proposal:
            proposal = llm_proposal["fields"]
            detected = {skill.id for skill in skills if skill.code in llm_proposal["skills"]}
    document_id = uuid4()
    storage = request.app.state.settings.storage_dir.resolve()
    storage.mkdir(parents=True, exist_ok=True)
    path = storage / f"{document_id}.pdf"
    await asyncio.to_thread(path.write_bytes, data)
    row = ResumeDocument(
        id=document_id,
        candidate_id=user.id,
        kind=enums.ResumeKind.UPLOADED,
        file_name=Path(file.filename or "resume.pdf").name[:255],
        storage_key=path.name,
        mime_type="application/pdf",
        size_bytes=len(data),
        raw_text=raw,
        parse_status=enums.ParseStatus.DONE,
    )
    session.add(row)
    await session.flush()
    parsed = ResumeParseResult(
        document_id=row.id,
        payload={
            "fields": proposal,
            "skill_ids": sorted(detected),
            "extraction_verified": bool(llm_proposal),
            "evidence": llm_proposal["evidence"] if llm_proposal else {},
            "truncated": llm_proposal["truncated"] if llm_proposal else False,
        },
        model=(
            request.app.state.settings.llm.resume_parser.model[-60:] if llm_proposal else "rules-v1"
        ),
    )
    session.add(parsed)
    await session.commit()
    return {
        "document": record(row, ["raw_text", "storage_key"]),
        "parse": record(parsed),
        "note": "Предложения не изменяют анкету автоматически. Подтвердите выбранные поля перед переносом",
    }


@router.get("/me/resumes", summary="Собственные документы без публичного хранилища")
async def resumes(user: User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    return [
        record(row, ["storage_key", "raw_text"])
        for row in (
            await session.scalars(
                select(ResumeDocument).where(ResumeDocument.candidate_id == user.id)
            )
        ).all()
    ]


@router.get("/me/resumes/{document_id}", summary="Скачать собственное исходное резюме")
async def download(
    document_id: UUID,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    row = await found(session, ResumeDocument, document_id)
    if row.candidate_id != user.id:
        raise HTTPException(404, "Документ не найден")
    storage = request.app.state.settings.storage_dir.resolve()
    path = (storage / row.storage_key).resolve()
    if not path.is_relative_to(storage) or not path.is_file():
        raise HTTPException(404, "Файл недоступен")
    return Response(
        await asyncio.to_thread(path.read_bytes),
        media_type="application/pdf",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": 'attachment; filename="resume.pdf"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/me/resume-parses/{parse_id}/apply", summary="Подтвердить выбранные предложения распознавания"
)
async def apply(
    parse_id: int,
    body: ApplyParse,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    parsed = await found(session, ResumeParseResult, parse_id, lock=True)
    document = await found(session, ResumeDocument, parsed.document_id)
    if document.candidate_id != user.id:
        raise HTTPException(404, "Разбор не найден")
    if parsed.applied_at:
        return {"status": "done"}
    proposal = parsed.payload
    if set(body.accept_fields) - set(proposal["fields"]) or set(body.accept_skill_ids) - set(
        proposal["skill_ids"]
    ):
        raise HTTPException(422, "Запрошены поля, которых нет в предложении")
    candidate = await candidate_for(session, user)
    fields = {name: proposal["fields"][name] for name in body.accept_fields}
    validated = ProfileUpdate.model_validate(fields)
    assign(candidate, validated.model_dump(exclude_unset=True))
    for skill_id in set(body.accept_skill_ids):
        if await session.get(CandidateSkill, (user.id, skill_id)) is None:
            session.add(
                CandidateSkill(
                    candidate_id=user.id,
                    skill_id=skill_id,
                    self_level=0,
                    source=enums.SkillSource.RESUME,
                )
            )
    parsed.applied_at = now()
    await session.commit()
    return {"status": "done"}


async def pdf_response(value, request):
    await request.app.state.pdf_limit.acquire()
    try:
        data = await asyncio.to_thread(render_pdf, value, request.app.state.settings.brand_dir)
    finally:
        request.app.state.pdf_limit.release()
    return Response(
        data,
        media_type="application/pdf",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": 'attachment; filename="fsp-profile.pdf"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/me/profile.pdf", summary="Полный собственный PDF-профиль по брендбуку ФСП")
async def own_pdf(
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    value = await card(session, user.id, request.app.state.settings, owner=True)
    await session.commit()
    return await pdf_response(value, request)


@router.get(
    "/candidates/{candidate_id}/profile.pdf", summary="PDF-профиль с текущими правами работодателя"
)
async def employer_pdf(
    candidate_id: UUID,
    request: Request,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    employer = await employer_for(session, user)
    value = await card(session, candidate_id, request.app.state.settings, employer.company_id)
    await session.commit()
    return await pdf_response(value, request)
