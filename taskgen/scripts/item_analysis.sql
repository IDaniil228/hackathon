-- Анализ заданий по оценённым сессиям: p — доля решивших задание шаблона, d — индекс
-- дискриминативности, разность долей решивших в верхних и нижних 27 % кандидатов.
-- Кандидаты ранжируются по среднему баллу сессии внутри пары (область, заявленный грейд).
-- Решённость берётся из результата сессии, то есть с порогом, действовавшим при оценке.
--   docker compose exec -T db psql -U taskgen -d taskgen < scripts/item_analysis.sql
WITH item AS (
    SELECT s.id AS session_id, s.area_code, s.claimed_grade_code, t.name AS template,
           (e ->> 'solved')::boolean::int AS solved, (e ->> 'score')::float AS score
    FROM taskgen.test_sessions s
    CROSS JOIN jsonb_array_elements(s.result -> 'tasks') AS e
    JOIN taskgen.tasks k ON k.id = (e ->> 'task_id')::uuid
    JOIN taskgen.templates t ON t.id = k.template_id
    WHERE s.status = 'scored'
),
ranked AS (
    SELECT session_id, percent_rank() OVER (
        PARTITION BY area_code, claimed_grade_code ORDER BY avg(score)
    ) AS rank
    FROM item
    GROUP BY session_id, area_code, claimed_grade_code
)
SELECT template, count(*) AS answers, round(avg(solved), 2) AS p,
       round(avg(solved) FILTER (WHERE rank >= 0.73)
             - avg(solved) FILTER (WHERE rank <= 0.27), 2) AS d
FROM item JOIN ranked USING (session_id)
GROUP BY template
ORDER BY template;
