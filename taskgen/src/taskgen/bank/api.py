"""HTTP API банка шаблонов: загрузка md-шаблонов и просмотр метаданных.

Тело шаблона содержит эталон и генератор тестов, поэтому client-ключ видит только
метаданные: утечка эталона через основной бэкенд обесценила бы задание.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from starlette.datastructures import UploadFile

from taskgen.auth import AUTH_ERRORS, Role, require
from taskgen.bank.embeddings import refresh_embeddings_safely
from taskgen.bank.importer import ImportOutcome, ImportStatus, TemplateDocument, import_documents
from taskgen.bank.parser import TemplateError
from taskgen.bank.validation import TemplateValidator
from taskgen.contracts import Embedder, TaskKind
from taskgen.db import Template
from taskgen.sandbox.errors import SandboxServiceError

router = APIRouter(prefix="/templates", tags=["Шаблоны"], responses=AUTH_ERRORS)

MARKDOWN_TYPES = ("text/markdown", "text/plain")


class TemplateUpload(BaseModel):
    body: str = Field(description="md-шаблон целиком: frontmatter и разделы")
    source: str | None = Field(default=None, description="Имя файла для сообщений об ошибках")


class BulkUpload(BaseModel):
    templates: list[TemplateUpload]


class ImportResult(BaseModel):
    source: str
    name: str
    version: int
    status: ImportStatus = Field(
        description="created — новый шаблон, updated — новая версия, unchanged — тело не изменилось"
    )
    template_id: uuid.UUID

    @classmethod
    def of(cls, outcome: ImportOutcome) -> ImportResult:
        return cls(
            source=outcome.source,
            name=outcome.name,
            version=outcome.version,
            status=outcome.status,
            template_id=outcome.template_id,
        )


class BulkImportResult(BaseModel):
    results: list[ImportResult]


class TemplateInfo(BaseModel):
    """Метаданные текущей версии шаблона."""

    id: uuid.UUID
    name: str
    version: int
    area: str
    grade: str
    kind: TaskKind
    runtime: str | None
    difficulty: int
    skills: list[str]
    description: str
    content_hash: str
    created_at: datetime
    body: str | None = Field(default=None, description="Исходный md; только для роли admin")

    @classmethod
    def of(cls, row: Template, *, with_body: bool) -> TemplateInfo:
        return cls(
            id=row.id,
            name=row.name,
            version=row.version,
            area=row.area_code,
            grade=row.grade_code,
            kind=TaskKind(row.kind),
            runtime=row.runtime,
            difficulty=row.difficulty,
            skills=list(row.skills),
            description=row.description,
            content_hash=row.content_hash,
            created_at=row.created_at,
            body=row.body if with_body else None,
        )


_UPLOAD_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"description": "Тело запроса не читается: неверный JSON или не UTF-8"},
    415: {"description": "Неподдерживаемый Content-Type"},
    422: {
        "description": (
            "Шаблон не прошёл проверку. detail.errors перечисляет все проблемы: файл (source), "
            "поле frontmatter, раздел тела или seed прогона в песочнице (location) и причину "
            "(message)"
        )
    },
    503: {"description": "Песочница недоступна: шаблоны с кодом не проверены, ничего не записано"},
}


@router.post(
    "",
    response_model=ImportResult,
    summary="Загрузить шаблон",
    description=(
        "Принимает один md-шаблон: телом запроса с Content-Type text/markdown или JSON "
        "с полем body. Шаблон ищется по имени из frontmatter. Если тело не изменилось, "
        "ничего не пишется; иначе создаётся следующая версия, а прежняя перестаёт быть "
        "текущей и остаётся в БД. Шаблон с кодом до записи проверяется в песочнице на "
        "нескольких seed: верные решения проходят все кейсы, неверные ловятся; на это уходят "
        "секунды, у шаблона на C++ — до десятков секунд. Эмбеддинг описания считается после "
        "ответа; его сбой не отменяет загрузку"
    ),
    responses=_UPLOAD_ERRORS,
    dependencies=[Depends(require(Role.ADMIN))],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "text/markdown": {"schema": {"type": "string"}},
                "application/json": {"schema": TemplateUpload.model_json_schema()},
            },
        }
    },
)
async def upload_template(request: Request, background: BackgroundTasks) -> ImportResult:
    content_type = _content_type(request)
    if content_type in MARKDOWN_TYPES:
        doc = TemplateDocument("запрос", _decode(await request.body()))
    elif content_type == "application/json":
        upload = _parse_json(await request.body(), TemplateUpload)
        doc = TemplateDocument(upload.source or "запрос", upload.body)
    else:
        raise _unsupported(content_type, ("text/markdown", "application/json"))
    [outcome] = await _import(request, background, [doc])
    return ImportResult.of(outcome)


@router.post(
    "/bulk",
    response_model=BulkImportResult,
    summary="Загрузить несколько шаблонов",
    description=(
        "Принимает файлы в multipart/form-data (поле files, по одному md на файл) или JSON "
        "со списком templates. Набор проверяется целиком, шаблоны с кодом — и в песочнице: "
        "при ошибке в любом файле не пишется ничего, а ответ перечисляет проблемы всех файлов. "
        "Ответ ждёт проверки всех шаблонов с кодом: на банк из seed/templates уходит около "
        "минуты"
    ),
    responses=_UPLOAD_ERRORS,
    dependencies=[Depends(require(Role.ADMIN))],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "files": {
                                "type": "array",
                                "items": {"type": "string", "format": "binary"},
                            }
                        },
                        "required": ["files"],
                    }
                },
                "application/json": {"schema": BulkUpload.model_json_schema()},
            },
        }
    },
)
async def upload_bulk(request: Request, background: BackgroundTasks) -> BulkImportResult:
    content_type = _content_type(request)
    docs: list[TemplateDocument] = []
    if content_type == "multipart/form-data":
        async with request.form() as form:
            for index, item in enumerate(form.getlist("files"), start=1):
                if not isinstance(item, UploadFile):
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST, "Поле files должно быть файлом"
                    )
                source = item.filename or f"файл {index}"
                docs.append(TemplateDocument(source, _decode(await item.read(), source)))
    elif content_type == "application/json":
        bulk = _parse_json(await request.body(), BulkUpload)
        docs = [
            TemplateDocument(t.source or f"шаблон {i}", t.body)
            for i, t in enumerate(bulk.templates, start=1)
        ]
    else:
        raise _unsupported(content_type, ("multipart/form-data", "application/json"))
    outcomes = await _import(request, background, docs)
    return BulkImportResult(results=[ImportResult.of(o) for o in outcomes])


@router.get(
    "",
    response_model=list[TemplateInfo],
    response_model_exclude_none=True,
    summary="Список шаблонов",
    description=(
        "Текущие версии шаблонов с фильтрами. Возвращаются только метаданные, без тела "
        "шаблона: эталон и тесты не выдаются"
    ),
    dependencies=[Depends(require(Role.CLIENT))],
)
async def list_templates(
    request: Request,
    area: Annotated[str | None, Query(description="Код области")] = None,
    grade: Annotated[str | None, Query(description="Код грейда")] = None,
    skill: Annotated[str | None, Query(description="Код навыка, который есть у шаблона")] = None,
    kind: Annotated[TaskKind | None, Query(description="Тип задания")] = None,
) -> list[TemplateInfo]:
    query = select(Template).where(Template.is_current).order_by(Template.name)
    if area is not None:
        query = query.where(Template.area_code == area)
    if grade is not None:
        query = query.where(Template.grade_code == grade)
    if skill is not None:
        query = query.where(Template.skills.contains([skill]))
    if kind is not None:
        query = query.where(Template.kind == kind.value)
    async with request.app.state.sessionmaker() as session:
        rows = await session.scalars(query)
        return [TemplateInfo.of(row, with_body=False) for row in rows]


@router.get(
    "/{name}",
    response_model=TemplateInfo,
    response_model_exclude_none=True,
    summary="Шаблон по имени",
    description=(
        "Метаданные текущей версии шаблона. Поле body с исходным md отдаётся только роли admin"
    ),
    responses={404: {"description": "Шаблона с таким именем нет"}},
)
async def get_template(
    name: str, request: Request, role: Annotated[Role, Depends(require(Role.CLIENT))]
) -> TemplateInfo:
    async with request.app.state.sessionmaker() as session:
        row = await session.scalar(
            select(Template).where(Template.name == name, Template.is_current)
        )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Шаблона {name!r} нет")
    return TemplateInfo.of(row, with_body=role is Role.ADMIN)


async def _import(
    request: Request, background: BackgroundTasks, docs: list[TemplateDocument]
) -> list[ImportOutcome]:
    if not docs:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Нет ни одного шаблона")
    state = request.app.state
    check = TemplateValidator.from_settings(getattr(state, "sandbox", None), state.settings)
    try:
        async with state.sessionmaker() as session, session.begin():
            outcomes = await import_documents(session, docs, check=check)
    except SandboxServiceError as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Шаблоны с кодом не проверены, ничего не записано: {error}",
        ) from error
    except TemplateError as error:
        errors = [
            {"source": i.source, "location": i.location, "message": i.message} for i in error.issues
        ]
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"message": "Шаблон не прошёл проверку", "errors": errors},
        ) from error
    if any(o.status is not ImportStatus.UNCHANGED for o in outcomes):
        # Эмбеддер может отвечать секундами; ответ на загрузку его не ждёт.
        background.add_task(
            refresh_embeddings_safely, request.app.state.sessionmaker, _embedder(request)
        )
        # Подписчик — пул генерации: новые версии шаблонов получают запас экземпляров.
        notify = getattr(request.app.state, "on_templates_imported", None)
        if notify is not None:
            notify([o.template_id for o in outcomes if o.status is not ImportStatus.UNCHANGED])
    return outcomes


def _embedder(request: Request) -> Embedder:
    """Отдельный эмбеддер, если приложение его задало, иначе LLM-клиент: он умеет embed."""
    return getattr(request.app.state, "embedder", None) or request.app.state.llm


def _content_type(request: Request) -> str:
    return request.headers.get("content-type", "").split(";", 1)[0].strip().lower()


def _decode(raw: bytes, source: str = "тело запроса") -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{source}: текст не в UTF-8") from error


def _parse_json[M: BaseModel](raw: bytes, model: type[M]) -> M:
    try:
        return model.model_validate(json.loads(raw))
    except json.JSONDecodeError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Неверный JSON: {error.msg}") from error
    except ValidationError as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {"message": "Неверная структура JSON", "errors": error.errors(include_url=False)},
        ) from error


def _unsupported(content_type: str, allowed: tuple[str, ...]) -> HTTPException:
    return HTTPException(
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        f"Content-Type {content_type or 'не задан'} не поддерживается; "
        f"допустимы: {', '.join(allowed)}",
    )
