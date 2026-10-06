"""Импорт шаблонов в БД: проверка набора целиком и upsert по имени с версиями.

Набор пишется только если все файлы прошли проверку: частично загруженный банк хуже
незагруженного, потому что ошибку в нём никто не заметит.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from taskgen.bank.parser import TemplateError, TemplateIssue, content_hash, parse_template
from taskgen.contracts import TemplateSpec
from taskgen.db import Area, AreaSkill, Grade, Skill, Template

# Первый ключ рекомендательной блокировки отделяет блокировки импорта от любых других
# пользователей pg_advisory_xact_lock в той же БД; второй ключ — хэш имени шаблона.
_IMPORT_LOCK_SPACE = 0x7467


class ImportStatus(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class TemplateDocument:
    """Исходный md и место, откуда он пришёл: путь файла или имя загруженного файла."""

    source: str
    text: str


@dataclass(frozen=True)
class ImportOutcome:
    source: str
    name: str
    version: int
    status: ImportStatus
    template_id: uuid.UUID


def read_directory(path: Path) -> list[TemplateDocument]:
    """Все *.md в папке и подпапках; source — путь относительно папки."""
    files = sorted(p for p in path.rglob("*.md") if p.is_file())
    return [
        TemplateDocument(source=p.relative_to(path).as_posix(), text=p.read_text(encoding="utf-8"))
        for p in files
    ]


async def import_documents(
    session: AsyncSession, docs: list[TemplateDocument]
) -> list[ImportOutcome]:
    """Проверяет все документы и пишет их в сессию; фиксирует транзакцию вызывающий.

    При любой ошибке бросает TemplateError со всеми проблемами всех файлов и ничего не пишет.
    """
    parsed, issues = _parse_all(docs)
    issues += await _check_references(session, parsed)
    if issues:
        raise TemplateError(issues)
    return [await _upsert(session, doc, spec) for doc, spec in parsed]


def _parse_all(
    docs: list[TemplateDocument],
) -> tuple[list[tuple[TemplateDocument, TemplateSpec]], list[TemplateIssue]]:
    parsed: list[tuple[TemplateDocument, TemplateSpec]] = []
    issues: list[TemplateIssue] = []
    for doc in docs:
        try:
            parsed.append((doc, parse_template(doc.text, source=doc.source)))
        except TemplateError as error:
            issues += error.issues
    counts = Counter(spec.name for _, spec in parsed)
    for doc, spec in parsed:
        if counts[spec.name] > 1:
            issues.append(
                TemplateIssue(
                    "name", f"имя {spec.name!r} встречается в наборе несколько раз", doc.source
                )
            )
    return parsed, issues


async def _check_references(
    session: AsyncSession, parsed: list[tuple[TemplateDocument, TemplateSpec]]
) -> list[TemplateIssue]:
    if not parsed:
        return []
    areas = set(await session.scalars(select(Area.code)))
    grades = set(await session.scalars(select(Grade.code)))
    skills = set(await session.scalars(select(Skill.code)))
    rows = await session.execute(select(AreaSkill.area_code, AreaSkill.skill_code))
    area_skills = {(area, skill) for area, skill in rows}
    issues: list[TemplateIssue] = []
    for doc, spec in parsed:
        if spec.area not in areas:
            issues.append(
                TemplateIssue("area", f"нет области {spec.area!r} в справочнике", doc.source)
            )
        if spec.grade not in grades:
            issues.append(
                TemplateIssue("grade", f"нет грейда {spec.grade!r} в справочнике", doc.source)
            )
        unknown = [code for code in spec.skills if code not in skills]
        if unknown:
            names = ", ".join(repr(code) for code in unknown)
            issues.append(
                TemplateIssue("skills", f"нет навыков в справочнике: {names}", doc.source)
            )
        # Навык вне области шаблона искажает отбор по сходству навыков; связь задаёт
        # area_skills, так что новый навык области добавляется строкой данных.
        foreign = [
            code
            for code in spec.skills
            if code in skills and spec.area in areas and (spec.area, code) not in area_skills
        ]
        if foreign:
            names = ", ".join(repr(code) for code in foreign)
            issues.append(
                TemplateIssue(
                    "skills", f"навыки не относятся к области {spec.area!r}: {names}", doc.source
                )
            )
    return issues


async def _upsert(
    session: AsyncSession, doc: TemplateDocument, spec: TemplateSpec
) -> ImportOutcome:
    digest = content_hash(doc.text)
    # Импорт одного имени идёт строго по очереди. Блокировка строки здесь не годится: у нового
    # имени строки ещё нет, а после чужого commit заблокированная строка перестаёт быть
    # текущей, и второй импорт упирается в templates_current_uq вместо новой версии.
    await session.execute(
        select(func.pg_advisory_xact_lock(_IMPORT_LOCK_SPACE, func.hashtext(spec.name)))
    )
    current = await session.scalar(
        select(Template).where(Template.name == spec.name, Template.is_current)
    )
    if current is not None and current.content_hash == digest:
        return ImportOutcome(
            doc.source, spec.name, current.version, ImportStatus.UNCHANGED, current.id
        )

    latest = await session.scalar(
        select(func.max(Template.version)).where(Template.name == spec.name)
    )
    if current is not None:
        current.is_current = False
        # Партиальный уникальный индекс templates_current_uq не пустит новую текущую версию,
        # пока старая не снята с текущих в этой же транзакции.
        await session.flush()
    row = Template(
        name=spec.name,
        version=(latest or 0) + 1,
        is_current=True,
        area_code=spec.area,
        grade_code=spec.grade,
        kind=spec.kind.value,
        runtime=spec.runtime,
        difficulty=spec.difficulty,
        skills=list(spec.skills),
        description=spec.description,
        body=doc.text,
        content_hash=digest,
    )
    session.add(row)
    await session.flush()
    status = ImportStatus.CREATED if current is None else ImportStatus.UPDATED
    return ImportOutcome(doc.source, spec.name, row.version, status, row.id)
