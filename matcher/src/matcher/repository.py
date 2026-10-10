"""Чтение данных api для подбора: потребность, префильтр, факты и карточки кандидатов.

Схему этих таблиц задают модели api (api/app/db/models). Здесь только запросы.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from matcher.factors import CandidateFacts, CandidateSkill, FspResult, NeedFacts, NeedSkill
from matcher.profile_text import Card, SkillLevel

# Позиция грейда в лестнице отрасли: расстояние между грейдами считается по позициям, а не
# по разности level, как в отборе шаблонов taskgen.
_GRADE_POSITIONS = """
    SELECT id, name, min_experience_months,
           row_number() OVER (PARTITION BY industry_id ORDER BY level) - 1 AS position
    FROM ref.grades
"""


@dataclass(frozen=True)
class Need:
    id: uuid.UUID
    is_active: bool
    title: str
    description: str
    specialization_id: int | None
    specialization_name: str | None
    grade_name: str | None
    grade_position: int | None
    grade_min_experience_months: int | None
    positive_prompt: str | None
    negative_prompt: str | None
    salary_min: int | None
    salary_max: int | None
    currency: str
    # Автор потребности: от его имени создаётся тред чата, если его ещё нет.
    author_user_id: uuid.UUID | None = None


_NEED = text(
    f"""
    SELECT n.id, n.is_active, n.title, n.description, n.author_user_id,
           coalesce(n.specialization_id, nc.specialization_id) AS specialization_id,
           s.name AS specialization_name,
           g.name AS grade_name, g.position AS grade_position,
           g.min_experience_months AS grade_min_experience_months,
           n.positive_prompt, n.negative_prompt, n.salary_min, n.salary_max, n.currency
    FROM hire.employer_needs n
    LEFT JOIN ref.categories nc ON nc.id = n.category_id
    LEFT JOIN ref.specializations s
           ON s.id = coalesce(n.specialization_id, nc.specialization_id)
    LEFT JOIN ({_GRADE_POSITIONS}) g ON g.id = coalesce(n.grade_id, nc.grade_id)
    WHERE n.id = :need_id
    """
)


async def load_need(session: AsyncSession, need_id: uuid.UUID) -> Need | None:
    row = (await session.execute(_NEED, {"need_id": need_id})).mappings().first()
    return Need(**row) if row is not None else None


_NEED_SKILLS = text(
    """
    SELECT ns.skill_id, sk.name, ns.importance::text = 'required' AS required, ns.min_level,
           sr.weight::float AS weight
    FROM hire.need_skills ns
    JOIN ref.skills sk ON sk.id = ns.skill_id
    LEFT JOIN match.skill_relevance sr
           ON sr.skill_id = ns.skill_id AND sr.specialization_id = :specialization_id
    WHERE ns.need_id = :need_id
    ORDER BY sk.name
    """
)


async def need_facts(session: AsyncSession, need: Need) -> NeedFacts:
    rows = await session.execute(
        _NEED_SKILLS, {"need_id": need.id, "specialization_id": need.specialization_id}
    )
    return NeedFacts(
        skills=tuple(NeedSkill(**row) for row in rows.mappings()),
        grade_position=need.grade_position,
        grade_name=need.grade_name,
        salary_min=need.salary_min,
        salary_max=need.salary_max,
        currency=need.currency,
    )


def need_card(need: Need, facts: NeedFacts) -> Card:
    """Карточка идеального кандидата из структурных полей потребности, без LLM."""
    category = ", ".join(part for part in (need.specialization_name, need.grade_name) if part)
    return Card(
        category=category or None,
        category_confirmed=True if need.grade_name else None,
        confirmed_skills=tuple(SkillLevel(skill.name, skill.min_level) for skill in facts.skills),
        experience_months=need.grade_min_experience_months,
        about=f"{need.title}. {need.description}",
        positive_prompt=need.positive_prompt,
        negative_prompt=need.negative_prompt,
    )


# Префильтр. Неподтверждённый грейд кандидата здесь не отсекается: он только ниже в порядке.
# Специализация кандидата — специализация его текущей категории, а без категории — желаемая.
# Вилки сравниваются в одной валюте; в разных валютах условие не применяется. Город важен,
# только если связанная вакансия не удалённая и город в ней задан: тогда кандидат живёт там
# или готов к переезду. Формат работы, который предпочитает кандидат, не отсекает его.
_PREFILTER = text(
    """
    WITH need AS (
        SELECT coalesce(n.specialization_id, nc.specialization_id) AS specialization_id,
               n.salary_min, n.salary_max, n.currency,
               v.work_format::text AS vacancy_format, v.city AS vacancy_city
        FROM hire.employer_needs n
        LEFT JOIN ref.categories nc ON nc.id = n.category_id
        LEFT JOIN hire.vacancies v ON v.id = n.vacancy_id
        WHERE n.id = :need_id
    )
    SELECT c.user_id, c.created_at
    FROM need
    CROSS JOIN profile.candidates c
    LEFT JOIN profile.candidate_privacy pr ON pr.candidate_id = c.user_id
    LEFT JOIN profile.candidate_preferences pf ON pf.candidate_id = c.user_id
    LEFT JOIN ref.categories cc ON cc.id = c.current_category_id
    WHERE coalesce(pr.is_searchable, true)
      AND c.is_open_to_offers
      AND (need.specialization_id IS NULL
           OR coalesce(cc.specialization_id, pf.desired_specialization_id) = need.specialization_id)
      AND (pf.currency IS DISTINCT FROM need.currency
           OR ((pf.salary_min IS NULL OR need.salary_max IS NULL
                OR pf.salary_min <= need.salary_max)
               AND (pf.salary_max IS NULL OR need.salary_min IS NULL
                    OR pf.salary_max >= need.salary_min)))
      AND (need.vacancy_format IS NULL OR need.vacancy_format = 'remote'
           OR need.vacancy_city IS NULL OR c.relocation
           OR lower(c.city) = lower(need.vacancy_city))
    ORDER BY c.user_id
    """
)


async def prefilter(session: AsyncSession, need_id: uuid.UUID) -> dict[uuid.UUID, datetime]:
    """Кандидаты, прошедшие жёсткие условия потребности: id → дата создания профиля."""
    rows = await session.execute(_PREFILTER, {"need_id": need_id})
    return {row.user_id: row.created_at for row in rows}


_CANDIDATES = text(
    f"""
    WITH strength AS (
        SELECT user_id,
               percent_rank() OVER w AS percentile,
               count(*) OVER (PARTITION BY current_category_id) AS peers
        FROM profile.candidates
        WHERE current_category_id IS NOT NULL
        WINDOW w AS (PARTITION BY current_category_id ORDER BY profile_strength)
    )
    SELECT c.user_id, c.experience_months, c.about, c.profile_refreshed_at,
           s.name AS specialization_name, g.name AS grade_name, g.position AS grade_position,
           coalesce(ga.source::text = 'test', false) AS grade_confirmed,
           pf.salary_min, pf.salary_max, pf.currency, pf.positive_prompt, pf.negative_prompt,
           CASE WHEN st.peers > 1 THEN st.percentile END AS strength_percentile,
           coalesce(pr.show_github, true) AS show_github,
           coalesce(pr.show_fsp, true) AS show_fsp
    FROM profile.candidates c
    LEFT JOIN ref.categories cc ON cc.id = c.current_category_id
    LEFT JOIN ref.specializations s ON s.id = cc.specialization_id
    LEFT JOIN ({_GRADE_POSITIONS}) g ON g.id = cc.grade_id
    LEFT JOIN assess.grade_assignments ga ON ga.candidate_id = c.user_id AND ga.is_current
    LEFT JOIN profile.candidate_preferences pf ON pf.candidate_id = c.user_id
    LEFT JOIN profile.candidate_privacy pr ON pr.candidate_id = c.user_id
    LEFT JOIN strength st ON st.user_id = c.user_id
    WHERE c.user_id = ANY(:ids)
    """
)

_SKILLS = text(
    """
    SELECT cs.candidate_id, cs.skill_id, sk.name, cs.self_level, cs.confirmed_level
    FROM profile.candidate_skills cs
    JOIN ref.skills sk ON sk.id = cs.skill_id
    WHERE cs.candidate_id = ANY(:ids)
    ORDER BY cs.candidate_id, sk.name
    """
)

_FSP = text(
    """
    SELECT a.candidate_id, e.title, e.level::text AS level, a.place, a.participants
    FROM profile.fsp_achievements a
    JOIN profile.fsp_events e ON e.id = a.event_id
    WHERE a.candidate_id = ANY(:ids)
    ORDER BY a.candidate_id, e.starts_on DESC NULLS LAST, e.title
    """
)

# Только открытые и собственные репозитории: форк с чужой историей не говорит о кандидате.
_REPOS = text(
    """
    SELECT candidate_id, full_name, description, primary_language, stars
    FROM profile.github_repos
    WHERE candidate_id = ANY(:ids) AND NOT is_private AND NOT is_fork
    ORDER BY candidate_id, stars DESC, full_name
    """
)

_FSP_LEVEL = {
    "local": "местный",
    "regional": "региональный",
    "federal": "всероссийский",
    "international": "международный",
}
GITHUB_REPOS_IN_CARD = 5


@dataclass(frozen=True)
class CandidateData:
    facts: CandidateFacts
    card: Card


async def candidates(
    session: AsyncSession, ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, CandidateData]:
    """Факты и карточки кандидатов ids, без machine summary.

    Флаги приватности действуют на обе части: скрытые GitHub и ФСП не попадают ни в текст
    карточки, ни в фактор ФСП.
    """
    params = {"ids": list(ids)}
    base = {row.user_id: row for row in await session.execute(_CANDIDATES, params)}
    skills: dict[uuid.UUID, list] = {cid: [] for cid in base}
    for row in await session.execute(_SKILLS, params):
        skills[row.candidate_id].append(row)
    fsp: dict[uuid.UUID, list] = {cid: [] for cid in base}
    for row in await session.execute(_FSP, params):
        if base[row.candidate_id].show_fsp:
            fsp[row.candidate_id].append(row)
    repos: dict[uuid.UUID, list] = {cid: [] for cid in base}
    for row in await session.execute(_REPOS, params):
        if base[row.candidate_id].show_github:
            repos[row.candidate_id].append(row)

    result = {}
    for cid, row in base.items():
        facts = CandidateFacts(
            skills={
                s.skill_id: CandidateSkill(s.self_level, s.confirmed_level) for s in skills[cid]
            },
            grade_position=row.grade_position,
            grade_name=row.grade_name,
            grade_confirmed=row.grade_confirmed,
            salary_min=row.salary_min,
            salary_max=row.salary_max,
            currency=row.currency,
            strength_percentile=row.strength_percentile,
            fsp=tuple(FspResult(f.title, f.place, f.participants) for f in fsp[cid]),
            refreshed_at=row.profile_refreshed_at,
        )
        category = ", ".join(p for p in (row.specialization_name, row.grade_name) if p)
        card = Card(
            category=category or None,
            category_confirmed=row.grade_confirmed if category else None,
            confirmed_skills=tuple(
                SkillLevel(s.name, s.confirmed_level)
                for s in skills[cid]
                if s.confirmed_level is not None
            ),
            claimed_skills=tuple(
                SkillLevel(s.name, s.self_level) for s in skills[cid] if s.confirmed_level is None
            ),
            experience_months=row.experience_months,
            about=row.about,
            positive_prompt=row.positive_prompt,
            negative_prompt=row.negative_prompt,
            github=tuple(_repo_line(r) for r in repos[cid][:GITHUB_REPOS_IN_CARD]),
            fsp=tuple(_fsp_line(f) for f in fsp[cid]),
        )
        result[cid] = CandidateData(facts=facts, card=card)
    return result


def _repo_line(row) -> str:
    details = ", ".join(
        part for part in (row.primary_language, f"звёзд {row.stars}" if row.stars else None) if part
    )
    line = row.full_name + (f": {row.description}" if row.description else "")
    return f"{line} ({details})" if details else line


def _fsp_line(row) -> str:
    line = f"{row.title} ({_FSP_LEVEL.get(row.level, row.level)} уровень)"
    if row.place is not None and row.participants:
        line += f", место {row.place} из {row.participants}"
    elif row.place is not None:
        line += f", место {row.place}"
    return line


@dataclass(frozen=True)
class ResultFilters:
    """Фильтры поверх готового прогона; None и пустой список — фильтра нет."""

    city: str | None = None
    relocation: bool | None = None
    work_format: str | None = None
    grade_confirmed: bool | None = None
    # Коды навыков ref.skills.code; кандидату нужны все, уровень не важен.
    skills: tuple[str, ...] = ()
    salary_max: int | None = None
    min_score: float | None = None


# Город сравнивается без учёта регистра. Кандидат без предпочтения формата работы проходит
# фильтр формата. Вилка сравнивается с нижней границей ожиданий кандидата в валюте
# потребности; кандидат без ожиданий или с ожиданиями в другой валюте её проходит.
_RESULTS = text(
    """
    SELECT r.candidate_id, r.rank, r.stage::text AS stage, r.vector_score::float AS vector_score,
           r.llm_score::float AS llm_score, r.final_score::float AS final_score,
           r.explanation, r.factors
    FROM match.search_results r
    JOIN match.search_runs run ON run.id = r.run_id
    JOIN hire.employer_needs n ON n.id = run.need_id
    JOIN profile.candidates c ON c.user_id = r.candidate_id
    LEFT JOIN profile.candidate_preferences pf ON pf.candidate_id = r.candidate_id
    WHERE r.run_id = :run_id AND r.rank IS NOT NULL AND r.is_shown
      AND (CAST(:city AS text) IS NULL OR lower(c.city) = lower(CAST(:city AS text)))
      AND (CAST(:relocation AS boolean) IS NULL OR c.relocation = CAST(:relocation AS boolean))
      AND (CAST(:work_format AS text) IS NULL OR c.work_format IS NULL
           OR c.work_format::text = CAST(:work_format AS text))
      AND (CAST(:grade_confirmed AS boolean) IS NULL
           OR (r.factors ->> 'grade_confirmed')::boolean = CAST(:grade_confirmed AS boolean))
      AND (CAST(:salary_max AS integer) IS NULL OR pf.salary_min IS NULL
           OR pf.currency IS DISTINCT FROM n.currency
           OR pf.salary_min <= CAST(:salary_max AS integer))
      AND (CAST(:min_score AS float8) IS NULL
           OR r.final_score::float8 >= CAST(:min_score AS float8))
      AND NOT EXISTS (
          SELECT 1 FROM unnest(CAST(:skills AS text[])) AS wanted(code)
          WHERE NOT EXISTS (
              SELECT 1 FROM profile.candidate_skills cs JOIN ref.skills s ON s.id = cs.skill_id
              WHERE cs.candidate_id = r.candidate_id AND s.code = wanted.code))
    ORDER BY r.rank
    """
)

_RESULTS_TOTAL = text(
    "SELECT count(*) FROM match.search_results "
    "WHERE run_id = :run_id AND rank IS NOT NULL AND is_shown"
)


async def run_results(
    session: AsyncSession, run_id: uuid.UUID, filters: ResultFilters
) -> tuple[int, list[dict]]:
    """Число строк выдачи прогона и строки, которые прошли filters, по возрастанию rank."""
    total = await session.scalar(_RESULTS_TOTAL, {"run_id": run_id})
    rows = await session.execute(
        _RESULTS,
        {
            "run_id": run_id,
            "city": filters.city,
            "relocation": filters.relocation,
            "work_format": filters.work_format,
            "grade_confirmed": filters.grade_confirmed,
            "salary_max": filters.salary_max,
            "min_score": filters.min_score,
            "skills": list(filters.skills),
        },
    )
    return total, [dict(row) for row in rows.mappings()]


_SEARCHABLE = text(
    """
    SELECT c.user_id
    FROM profile.candidates c
    LEFT JOIN profile.candidate_privacy pr ON pr.candidate_id = c.user_id
    WHERE coalesce(pr.is_searchable, true) AND c.is_open_to_offers
    ORDER BY c.user_id
    """
)


async def searchable_candidates(session: AsyncSession) -> list[uuid.UUID]:
    """Все кандидаты, которых вообще можно найти: вход команды matcher embed."""
    return list((await session.scalars(_SEARCHABLE)).all())
