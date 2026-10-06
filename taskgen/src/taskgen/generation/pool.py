"""Пул стандартных экземпляров: запас для первого задания сессии.

На слабой машине одна легенда пишется минутами, а кандидат ждёт ответа на создание сессии.
Поэтому для каждого текущего шаблона заранее готовится несколько экземпляров без
персонализации, проверенных эталоном так же, как экземпляр сессии. Экземпляр выдаётся одной
сессии и удаляется из пула в той же транзакции; устаревший не выдаётся и удаляется при
пополнении.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from collections.abc import Awaitable, Callable, Iterable
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from taskgen.bank.parser import TemplateError, parse_template
from taskgen.contracts import TemplateSpec
from taskgen.db import PoolInstance, Template
from taskgen.generation.instance import (
    SEED_BITS,
    GenerationError,
    TemplateDefectError,
    generate_instance,
)

if TYPE_CHECKING:
    from taskgen.generation.service import Generator

logger = logging.getLogger(__name__)

# Поля экземпляра, которые переходят из пула в задание без изменений.
INSTANCE_COLUMNS = (
    "kind",
    "statement",
    "params",
    "seed",
    "public_payload",
    "hidden_payload",
    "generated_by",
    "validated",
)


def template_spec(template: Template) -> TemplateSpec:
    """Разобранный шаблон; шаблон, который не разбирается, — дефект шаблона."""
    try:
        return parse_template(template.body, source=template.name)
    except TemplateError as error:
        raise TemplateDefectError(f"Шаблон {template.name} не разбирается: {error}") from error


async def take(
    db: AsyncSession, template_id: uuid.UUID, *, max_age: timedelta
) -> dict[str, Any] | None:
    """Забирает из пула самый старый свежий экземпляр шаблона: строка удаляется и возвращается.

    FOR UPDATE SKIP LOCKED: параллельная выдача берёт другую строку или ничего, но не ту же.
    Удаление фиксируется вместе с транзакцией вызывающего, где экземпляр становится заданием.
    """
    oldest = (
        select(PoolInstance.id)
        .where(
            PoolInstance.template_id == template_id,
            PoolInstance.created_at > func.now() - max_age,
        )
        .order_by(PoolInstance.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    columns = [PoolInstance.__table__.c[name] for name in ("id", *INSTANCE_COLUMNS)]
    row = (
        (
            await db.execute(
                delete(PoolInstance).where(PoolInstance.id == oldest).returning(*columns)
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row is not None else None


async def refill(
    gen: Generator,
    *,
    template_ids: Iterable[uuid.UUID] | None = None,
    before_each: Callable[[], Awaitable[None]] | None = None,
) -> int:
    """Удаляет устаревшие экземпляры и догенерирует пул до pool_size; возвращает, сколько создано.

    template_ids ограничивает пополнение этими шаблонами, например только что загруженными.
    before_each вызывается перед каждой генерацией: так сессии кандидатов идут к модели раньше
    пула. Шаблон, который не дал экземпляра, пропускается до следующего пополнения.
    """
    options = gen.options
    max_age = timedelta(hours=options.pool_max_age_hours)
    async with gen.sessionmaker() as db, db.begin():
        await db.execute(
            delete(PoolInstance).where(
                or_(
                    PoolInstance.created_at <= func.now() - max_age,
                    # Новая версия шаблона заменяет старую, и экземпляры старой не выдаются.
                    PoolInstance.template_id.not_in(select(Template.id).where(Template.is_current)),
                )
            )
        )
    if options.pool_size == 0:
        return 0
    async with gen.sessionmaker() as db:
        query = select(Template).where(Template.is_current).order_by(Template.name)
        if template_ids is not None:
            query = query.where(Template.id.in_(list(template_ids)))
        templates = list(await db.scalars(query))
        counts: dict[uuid.UUID, int] = dict(
            (
                await db.execute(
                    select(PoolInstance.template_id, func.count()).group_by(
                        PoolInstance.template_id
                    )
                )
            ).all()
        )
    deps = gen.instance_deps()
    made = 0
    for template in templates:
        for _ in range(options.pool_size - counts.get(template.id, 0)):
            if before_each is not None:
                await before_each()
            try:
                spec = template_spec(template)
                instance = await generate_instance(spec, secrets.randbits(SEED_BITS), deps)
            except GenerationError as error:
                logger.warning("Пул: шаблон %s не дал экземпляра: %s", template.name, error)
                break
            async with gen.sessionmaker() as db, db.begin():
                db.add(PoolInstance(template_id=template.id, **instance.columns()))
            made += 1
    return made
