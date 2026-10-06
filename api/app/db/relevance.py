"""Пересчёт веса навыка в специализации.

Отвечает на вопрос «кто проставляет вес»: никто. В справочнике
``ref.specialization_skills`` лежит только экспертное булево ``is_core`` —
«навык профильный или сопутствующий». Числовой вес выводится отсюда из
наблюдаемого спроса.

Формула — байесовское сглаживание доли упоминаний::

    weight = (need_mentions + K * prior) / (need_total + K)

* ``need_mentions`` — в скольких потребностях и вакансиях специализации
  навык указан как требуемый;
* ``need_total`` — сколько всего потребностей и вакансий у специализации;
* ``prior`` — ``PRIOR_CORE`` для профильных навыков, ``PRIOR_SECONDARY``
  для остальных;
* ``K`` — ``PRIOR_WEIGHT``, насколько сильно априорное мнение держится
  против данных.

Пока по специализации мало вакансий, вес тянется к экспертному ``is_core`` —
система осмысленно работает на холодном старте. Когда данных набралось, их
доля перевешивает, и вес отражает реальный спрос. Руками числа не вводит
никто и никогда.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: Априорный вес профильного навыка
PRIOR_CORE = 1.0
#: Априорный вес сопутствующего навыка
PRIOR_SECONDARY = 0.25
#: Сколько «виртуальных наблюдений» стоит экспертное мнение.
#: K = 5 означает: после пяти реальных вакансий данные весят столько же,
#: сколько справочник, после двадцати — вчетверо больше.
PRIOR_WEIGHT = 5

RECOMPUTE_SQL = text(
    """
    WITH spec_demand AS (
        -- сколько потребностей и вакансий заведено на каждую специализацию
        SELECT s.id AS specialization_id,
               count(DISTINCT n.id) + count(DISTINCT v.id) AS need_total
        FROM ref.specializations s
        LEFT JOIN hire.employer_needs n ON n.specialization_id = s.id
        LEFT JOIN ref.categories c      ON c.specialization_id = s.id
        LEFT JOIN hire.vacancies v      ON v.category_id = c.id
        GROUP BY s.id
    ),
    skill_demand AS (
        -- в скольких из них навык указан как требуемый
        SELECT s.id AS specialization_id, sk.skill_id,
               count(DISTINCT sk.need_id) + count(DISTINCT vs.vacancy_id) AS need_mentions
        FROM ref.specializations s
        JOIN hire.employer_needs n ON n.specialization_id = s.id
        JOIN hire.need_skills sk   ON sk.need_id = n.id
        LEFT JOIN ref.categories c ON c.specialization_id = s.id
        LEFT JOIN hire.vacancies v ON v.category_id = c.id
        LEFT JOIN hire.vacancy_skills vs
               ON vs.vacancy_id = v.id AND vs.skill_id = sk.skill_id
        GROUP BY s.id, sk.skill_id
    ),
    candidate_supply AS (
        -- у скольких кандидатов специализации навык подтверждён тестом
        SELECT c.specialization_id, cs.skill_id, count(*) AS candidate_mentions
        FROM profile.candidate_skills cs
        JOIN profile.candidates cand ON cand.user_id = cs.candidate_id
        JOIN ref.categories c        ON c.id = cand.current_category_id
        WHERE cs.confirmed_level IS NOT NULL
        GROUP BY c.specialization_id, cs.skill_id
    ),
    -- множество пар берём из справочника и из наблюдений разом: навык мог
    -- появиться в вакансиях, ещё не попав в ref.specialization_skills
    pairs AS (
        SELECT specialization_id, skill_id FROM ref.specialization_skills
        UNION
        SELECT specialization_id, skill_id FROM skill_demand
    )
    INSERT INTO match.skill_relevance AS sr
        (specialization_id, skill_id, need_mentions, need_total, candidate_mentions, weight, computed_at)
    SELECT p.specialization_id,
           p.skill_id,
           coalesce(sd.need_mentions, 0),
           coalesce(d.need_total, 0),
           coalesce(cs.candidate_mentions, 0),
           -- оба операнда приводим к numeric: round(double precision, int)
           -- в PostgreSQL нет, а параметры приезжают как double precision
           round(
               (coalesce(sd.need_mentions, 0)
                + :k * CASE WHEN ss.is_core THEN :prior_core ELSE :prior_secondary END)::numeric
               / (coalesce(d.need_total, 0) + :k)::numeric,
               3
           ),
           now()
    FROM pairs p
    LEFT JOIN ref.specialization_skills ss
           ON ss.specialization_id = p.specialization_id AND ss.skill_id = p.skill_id
    LEFT JOIN spec_demand d  ON d.specialization_id = p.specialization_id
    LEFT JOIN skill_demand sd
           ON sd.specialization_id = p.specialization_id AND sd.skill_id = p.skill_id
    LEFT JOIN candidate_supply cs
           ON cs.specialization_id = p.specialization_id AND cs.skill_id = p.skill_id
    ON CONFLICT (specialization_id, skill_id) DO UPDATE SET
        need_mentions      = excluded.need_mentions,
        need_total         = excluded.need_total,
        candidate_mentions = excluded.candidate_mentions,
        weight             = excluded.weight,
        computed_at        = excluded.computed_at
    """
)


async def recompute_skill_relevance(session: AsyncSession) -> int:
    """Пересчитать веса для всех пар «специализация — навык».

    Возвращает число обновлённых строк. Вызывается по расписанию или после
    заметного изменения набора вакансий и потребностей.
    """
    result = await session.execute(
        RECOMPUTE_SQL,
        {"k": PRIOR_WEIGHT, "prior_core": PRIOR_CORE, "prior_secondary": PRIOR_SECONDARY},
    )
    return result.rowcount or 0
