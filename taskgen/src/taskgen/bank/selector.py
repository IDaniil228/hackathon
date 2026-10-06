"""Отбор шаблонов для теста: кандидаты из БД, параметры из test_configs и числовая часть.

Числа считает taskgen.bank.selection; здесь только сбор входов и выбор режима сходства.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskgen.bank.embeddings import embedding_text
from taskgen.bank.parser import content_hash
from taskgen.bank.selection import (
    cosine_similarity,
    distance,
    draw,
    grade_positions,
    jaccard_similarity,
    probabilities,
)
from taskgen.config import SelectionSettings
from taskgen.contracts import Embedder
from taskgen.db import Area, Grade, Skill, Template, TemplateEmbedding, TestConfig

logger = logging.getLogger(__name__)


class SelectionMode(StrEnum):
    """Чем измерено сходство шаблона с кандидатом."""

    EMBEDDINGS = "embeddings"
    # Запасной режим: доля общих навыков вместо косинуса эмбеддингов.
    SKILLS_JACCARD = "skills_jaccard"


class SelectionError(ValueError):
    """Отбор невозможен из-за входа: неизвестная область или грейд, неверные параметры."""


@dataclass(frozen=True)
class SelectedTemplate:
    template: Template
    grade_gap: int
    similarity: float
    distance: float
    # Вероятность выпасть первым из всего пула: по ней видно, насколько шаблон был ожидаем.
    probability: float
    # Вероятность на том шаге выборки без возвращения, где шаблон выпал.
    step_probability: float


@dataclass(frozen=True)
class RankedTemplate:
    template: Template
    probability: float


@dataclass(frozen=True)
class SelectionResult:
    selected: list[SelectedTemplate]
    selection_mode: SelectionMode
    fallback_reason: str | None
    parameters: SelectionSettings
    candidates: int
    # Все кандидаты по убыванию вероятности выпасть первым: отсюда генерация берёт замену
    # шаблону, который не дал экземпляра.
    ranking: list[RankedTemplate] = field(default_factory=list)

    def explanation(self) -> dict[str, Any]:
        """Сериализуемое описание выдачи для объяснимости результата теста."""
        return {
            "selection_mode": self.selection_mode.value,
            "fallback_reason": self.fallback_reason,
            "parameters": self.parameters.model_dump(),
            "candidates": self.candidates,
            "selected": [
                {
                    "template_id": str(item.template.id),
                    "name": item.template.name,
                    "version": item.template.version,
                    "grade": item.template.grade_code,
                    "grade_gap": item.grade_gap,
                    "similarity": item.similarity,
                    "distance": item.distance,
                    "probability": item.probability,
                    "step_probability": item.step_probability,
                }
                for item in self.selected
            ],
            "ranking": [
                {
                    "template_id": str(item.template.id),
                    "name": item.template.name,
                    "grade": item.template.grade_code,
                    "kind": item.template.kind,
                    "probability": item.probability,
                }
                for item in self.ranking
            ],
        }


async def select_templates(
    session: AsyncSession,
    *,
    area: str,
    grade: str,
    skills: list[str],
    rng: random.Random,
    settings: SelectionSettings,
    embedder: Embedder | None = None,
    k: int | None = None,
    temperature: float | None = None,
    alpha: float | None = None,
    beta: float | None = None,
    profile_skills: Sequence[str] = (),
    resume: str = "",
) -> SelectionResult:
    """Выбирает до k текущих шаблонов области: p ∝ exp(−d / T), без возвращения.

    Параметр берётся из аргумента, если он задан, иначе из test_configs для (area, grade),
    если поле там не NULL, иначе из settings. Сходство по эмбеддингам считается с текстом
    из навыков, навыков профиля и резюме; доля общих навыков — только по skills.
    """
    if await session.get(Area, area) is None:
        raise SelectionError(f"нет области {area!r} в справочнике")
    levels: dict[str, int] = dict((await session.execute(select(Grade.code, Grade.level))).all())
    if grade not in levels:
        raise SelectionError(f"нет грейда {grade!r} в справочнике")
    positions = grade_positions(levels)

    config = await session.get(TestConfig, (area, grade))
    parameters = _resolve_parameters(
        settings,
        config,
        tasks_per_test=k,
        temperature=temperature,
        alpha=alpha,
        beta=beta,
    )

    templates = list(
        await session.scalars(
            select(Template)
            .where(Template.area_code == area, Template.is_current)
            .order_by(Template.name)
        )
    )
    if not templates:
        return SelectionResult([], SelectionMode.EMBEDDINGS, None, parameters, 0)

    # Навыки профиля из других областей не меняют долю общих навыков с шаблонами области,
    # но для эмбеддинга кандидата они — часть картины.
    described = list(dict.fromkeys([*skills, *profile_skills]))
    similarities, reason = await _embedding_similarities(
        session, templates, described, resume, embedder
    )
    mode = SelectionMode.EMBEDDINGS
    if similarities is None:
        mode = SelectionMode.SKILLS_JACCARD
        similarities = [jaccard_similarity(t.skills, skills) for t in templates]

    gaps = [positions[t.grade_code] - positions[grade] for t in templates]
    distances = [
        distance(gap, sim, alpha=parameters.alpha, beta=parameters.beta)
        for gap, sim in zip(gaps, similarities, strict=True)
    ]
    initial = probabilities(distances, parameters.temperature)
    picks = draw(distances, parameters.tasks_per_test, parameters.temperature, rng)
    selected = [
        SelectedTemplate(
            template=templates[pick.index],
            grade_gap=gaps[pick.index],
            similarity=similarities[pick.index],
            distance=distances[pick.index],
            probability=initial[pick.index],
            step_probability=pick.probability,
        )
        for pick in picks
    ]
    # Порядок templates — по имени, а sorted устойчив: равные вероятности идут по имени.
    ranking = sorted(
        (RankedTemplate(t, p) for t, p in zip(templates, initial, strict=True)),
        key=lambda item: -item.probability,
    )
    return SelectionResult(selected, mode, reason, parameters, len(templates), ranking)


def _resolve_parameters(
    settings: SelectionSettings, config: TestConfig | None, **explicit: float | int | None
) -> SelectionSettings:
    values = settings.model_dump()
    for name in values:
        stored = getattr(config, name, None) if config is not None else None
        if explicit.get(name) is not None:
            values[name] = explicit[name]
        elif stored is not None:
            values[name] = stored
    try:
        # Повторная проверка моделью ловит неверные значения из аргументов и из test_configs.
        return SelectionSettings.model_validate(values)
    except ValidationError as error:
        problems = "; ".join(f"{e['loc'][0]}: {e['msg']}" for e in error.errors())
        raise SelectionError(f"неверные параметры отбора: {problems}") from error


async def _embedding_similarities(
    session: AsyncSession,
    templates: list[Template],
    skills: list[str],
    resume: str,
    embedder: Embedder | None,
) -> tuple[list[float] | None, str | None]:
    """Косинусы эмбеддингов или (None, причина), если сравнивать нечем.

    Режим общий для всего пула: смешивать косинус одних шаблонов с долей навыков других
    нельзя, у этих величин разные распределения.
    """
    if embedder is None:
        return None, "эмбеддер не подключён"
    if not skills and not resume.strip():
        return None, "у кандидата не указаны ни навыки, ни резюме"

    rows = await session.execute(
        select(
            TemplateEmbedding.template_id,
            TemplateEmbedding.content_hash,
            TemplateEmbedding.embedding,
        ).where(
            TemplateEmbedding.model == embedder.model_name,
            TemplateEmbedding.template_id.in_([t.id for t in templates]),
        )
    )
    cached = {template_id: (cached_hash, vector) for template_id, cached_hash, vector in rows}
    fresh: dict[Any, list[float]] = {}
    for t in templates:
        entry = cached.get(t.id)
        if entry is not None and entry[0] == content_hash(embedding_text(t.description)):
            fresh[t.id] = list(entry[1])
    missing = len(templates) - len(fresh)
    if missing:
        return None, (
            f"нет актуального эмбеддинга у {missing} шаблонов из {len(templates)} "
            f"(модель {embedder.model_name})"
        )

    titles: dict[str, str] = dict(
        (await session.execute(select(Skill.code, Skill.title).where(Skill.code.in_(skills)))).all()
    )
    # Навыки кандидата сравниваются с описанием шаблона, поэтому в эмбеддер идут названия
    # навыков, а не коды: "Два указателя" ближе к тексту описания, чем "two-pointers".
    # Резюме добавляет то, чего нет в списке навыков: опыт, предметные области, задачи.
    parts = []
    if skills:
        parts.append("Навыки: " + ", ".join(titles.get(code, code) for code in skills))
    if resume.strip():
        parts.append("Резюме: " + resume.strip())
    query = "\n\n".join(parts)
    try:
        [candidate] = await embedder.embed([query])
        return [cosine_similarity(fresh[t.id], candidate) for t in templates], None
    except Exception as error:
        logger.warning("Отбор без эмбеддингов: эмбеддер не посчитал навыки кандидата: %s", error)
        return None, f"эмбеддер вернул ошибку: {error}"
