-- =============================================================================
--  Платформа ФСП — демонстрационные данные
-- =============================================================================
--  Применять ПОСЛЕ db/schema.sql:
--      docker compose exec -T postgres psql -U fsp -d fsp < db/demo_data.sql
--
--  Файл рассчитан на чистую схему: повторное применение поверх себя упрётся в
--  уникальные ключи. Нужен чистый прогон — сначала db/schema.sql.
-- =============================================================================

BEGIN;

-- =============================================================================
--  95. Демонстрационные данные
-- =============================================================================
--  Минимальный набор под сквозной сценарий из п. 3.3 ТЗ: регистрация соискателя,
--  опрос, тест, присвоение категории, поиск работодателем нужной категории,
--  выход на контакт, а также классический отклик на вакансию.
--
--  Среди кандидатов намеренно есть и участники ФСП, и люди без истории ФСП —
--  корректная обработка второго случая обязательна по ТЗ.
--
--  password_hash здесь — заглушка фиксированного вида, а не хеш реального
--  пароля: войти под этими учётками нельзя, они нужны для выборок.
-- =============================================================================

-- --- Пользователи ------------------------------------------------------------
INSERT INTO core.users (id, email, password_hash, status, email_verified_at, created_at) VALUES
    ('11111111-0000-4000-8000-000000000001', 'anna.kovaleva@example.com',  '$argon2id$v=19$m=65536,t=3,p=4$ZGVtb3NhbHRkZW1vc2FsdA$ZGVtb25vdGFyZWFsaGFzaGRlbW9ub3RhcmVhbA', 'active', now() - interval '60 days', now() - interval '60 days'),
    ('11111111-0000-4000-8000-000000000002', 'pavel.smirnov@example.com',  '$argon2id$v=19$m=65536,t=3,p=4$ZGVtb3NhbHRkZW1vc2FsdA$ZGVtb25vdGFyZWFsaGFzaGRlbW9ub3RhcmVhbA', 'active', now() - interval '40 days', now() - interval '40 days'),
    ('11111111-0000-4000-8000-000000000003', 'irina.volkova@example.com',  '$argon2id$v=19$m=65536,t=3,p=4$ZGVtb3NhbHRkZW1vc2FsdA$ZGVtb25vdGFyZWFsaGFzaGRlbW9ub3RhcmVhbA', 'active', now() - interval '35 days', now() - interval '35 days'),
    ('11111111-0000-4000-8000-000000000004', 'denis.orlov@example.com',    '$argon2id$v=19$m=65536,t=3,p=4$ZGVtb3NhbHRkZW1vc2FsdA$ZGVtb25vdGFyZWFsaGFzaGRlbW9ub3RhcmVhbA', 'active', now() - interval '30 days', now() - interval '30 days'),
    ('11111111-0000-4000-8000-000000000005', 'marina.lebedeva@example.com','$argon2id$v=19$m=65536,t=3,p=4$ZGVtb3NhbHRkZW1vc2FsdA$ZGVtb25vdGFyZWFsaGFzaGRlbW9ub3RhcmVhbA', 'active', now() - interval '25 days', now() - interval '25 days'),
    ('11111111-0000-4000-8000-000000000006', 'oleg.vasilev@example.com',   '$argon2id$v=19$m=65536,t=3,p=4$ZGVtb3NhbHRkZW1vc2FsdA$ZGVtb25vdGFyZWFsaGFzaGRlbW9ub3RhcmVhbA', 'active', now() - interval '10 days', now() - interval '10 days'),
    ('22222222-0000-4000-8000-000000000001', 'hr@technoline.example',      '$argon2id$v=19$m=65536,t=3,p=4$ZGVtb3NhbHRkZW1vc2FsdA$ZGVtb25vdGFyZWFsaGFzaGRlbW9ub3RhcmVhbA', 'active', now() - interval '90 days', now() - interval '90 days'),
    ('22222222-0000-4000-8000-000000000002', 'lead@datum.example',         '$argon2id$v=19$m=65536,t=3,p=4$ZGVtb3NhbHRkZW1vc2FsdA$ZGVtb25vdGFyZWFsaGFzaGRlbW9ub3RhcmVhbA', 'active', now() - interval '80 days', now() - interval '80 days');

INSERT INTO core.user_roles (user_id, role)
SELECT id, 'candidate' FROM core.users WHERE email LIKE '%@example.com';
INSERT INTO core.user_roles (user_id, role)
SELECT id, 'employer'  FROM core.users WHERE email LIKE '%.example';

INSERT INTO core.user_consents (user_id, kind, document_version)
SELECT id, 'pd_processing', 'v1.0' FROM core.users;
INSERT INTO core.user_consents (user_id, kind, document_version)
SELECT id, 'profile_publication', 'v1.0' FROM core.users WHERE email LIKE '%@example.com';

-- --- Кандидаты ---------------------------------------------------------------
INSERT INTO profile.candidates (
    user_id, first_name, last_name, middle_name, birth_date, phone, city,
    relocation, work_format, employment_type, experience_months, about,
    current_category_id, profile_strength, profile_refreshed_at
) VALUES
    ('11111111-0000-4000-8000-000000000001', 'Анна',  'Ковалёва', 'Сергеевна', DATE '1996-03-14', '+79161234501', 'Москва',
     false, 'hybrid', 'full_time', 54, 'Backend на Python: сервисы с высокой нагрузкой, PostgreSQL, проектирование API. Призёр командных соревнований ФСП.',
     (SELECT id FROM ref.categories WHERE slug = 'backend.middle'), 86.50, now() - interval '3 days'),

    ('11111111-0000-4000-8000-000000000002', 'Павел', 'Смирнов', NULL, DATE '2001-07-02', '+79161234502', 'Казань',
     true, 'remote', 'full_time', 11, 'Год коммерческой разработки на FastAPI, учебные проекты с PostgreSQL. Истории участия в ФСП нет.',
     (SELECT id FROM ref.categories WHERE slug = 'backend.junior'), 41.00, now() - interval '12 days'),

    ('11111111-0000-4000-8000-000000000003', 'Ирина', 'Волкова', 'Андреевна', DATE '1998-11-23', '+79161234503', 'Санкт-Петербург',
     false, 'remote', 'full_time', 38, 'Frontend на React и TypeScript, доступность интерфейсов, дизайн-системы.',
     (SELECT id FROM ref.categories WHERE slug = 'frontend.middle'), 72.00, now() - interval '6 days'),

    ('11111111-0000-4000-8000-000000000004', 'Денис', 'Орлов', 'Игоревич', DATE '1995-01-30', '+79161234504', 'Новосибирск',
     true, 'hybrid', 'full_time', 62, 'ML-инженер: NLP, retrieval, инференс в проде. Победитель регионального этапа ФСП.',
     (SELECT id FROM ref.categories WHERE slug = 'ml_engineer.middle'), 88.00, now() - interval '2 days'),

    ('11111111-0000-4000-8000-000000000005', 'Марина', 'Лебедева', NULL, DATE '1990-09-09', '+79161234505', 'Москва',
     false, 'office', 'full_time', 120, 'DevOps/SRE: Kubernetes, Terraform, наблюдаемость. Веду менторство внутри команды.',
     (SELECT id FROM ref.categories WHERE slug = 'devops.senior'), 79.50, now() - interval '20 days'),

    ('11111111-0000-4000-8000-000000000006', 'Олег', 'Васильев', NULL, DATE '2002-05-18', '+79161234506', 'Екатеринбург',
     true, 'remote', 'part_time', 6, 'Тестирование: тест-дизайн, автотесты на pytest. Истории участия в ФСП нет.',
     (SELECT id FROM ref.categories WHERE slug = 'qa.junior'), 33.00, now() - interval '9 days');

INSERT INTO profile.candidate_privacy (candidate_id, is_searchable, show_contacts, show_salary, show_fsp, show_github)
SELECT user_id, true, false, true, true, true FROM profile.candidates;

INSERT INTO profile.candidate_preferences (candidate_id, desired_specialization_id, salary_min, salary_max, positive_prompt, negative_prompt)
VALUES
    ('11111111-0000-4000-8000-000000000001', (SELECT id FROM ref.specializations WHERE code = 'backend'),      250000, 340000,
     'Продуктовая команда, зрелые процессы ревью, сложные данные', 'Аутстаффинг, овертаймы, legacy без тестов'),
    ('11111111-0000-4000-8000-000000000002', (SELECT id FROM ref.specializations WHERE code = 'backend'),      110000, 160000,
     'Наставник в команде, понятные задачи, обратная связь', 'Полная самостоятельность без поддержки'),
    ('11111111-0000-4000-8000-000000000003', (SELECT id FROM ref.specializations WHERE code = 'frontend'),     200000, 280000,
     'Дизайн-система, доступность, собственный продукт', 'Вёрстка лендингов на потоке'),
    ('11111111-0000-4000-8000-000000000004', (SELECT id FROM ref.specializations WHERE code = 'ml_engineer'),  280000, 400000,
     'Исследовательские задачи с выходом в прод, NLP', 'Только разметка данных'),
    ('11111111-0000-4000-8000-000000000005', (SELECT id FROM ref.specializations WHERE code = 'devops'),       300000, 420000,
     'Надёжность, SLO, автоматизация', 'Ручные релизы по ночам'),
    ('11111111-0000-4000-8000-000000000006', (SELECT id FROM ref.specializations WHERE code = 'qa'),            70000, 110000,
     'Обучение, менторство, автотесты', 'Только ручная регрессия');

INSERT INTO profile.candidate_links (candidate_id, kind, url, label) VALUES
    ('11111111-0000-4000-8000-000000000001', 'github',    'https://github.com/anna-kovaleva-demo', 'GitHub'),
    ('11111111-0000-4000-8000-000000000001', 'portfolio', 'https://kovaleva.example/portfolio',    'Портфолио'),
    ('11111111-0000-4000-8000-000000000003', 'github',    'https://github.com/irina-volkova-demo', 'GitHub'),
    ('11111111-0000-4000-8000-000000000004', 'telegram',  'https://t.me/denis_orlov_demo',         'Telegram'),
    ('11111111-0000-4000-8000-000000000005', 'gitlab',    'https://gitlab.com/m-lebedeva-demo',    'GitLab');

-- Навыки: self_level — заявленный, confirmed_level — подтверждённый тестом
INSERT INTO profile.candidate_skills (candidate_id, skill_id, self_level, confirmed_level, years, source)
SELECT m.candidate_id::uuid, s.id, m.self_level, m.confirmed_level, m.years, m.source::profile.skill_source
FROM (VALUES
    ('11111111-0000-4000-8000-000000000001', 'python',     5, 4, 4.5, 'test'),
    ('11111111-0000-4000-8000-000000000001', 'postgresql', 4, 4, 4.0, 'test'),
    ('11111111-0000-4000-8000-000000000001', 'fastapi',    4, NULL, 3.0, 'self'),
    ('11111111-0000-4000-8000-000000000001', 'docker',     3, NULL, 3.0, 'github'),
    ('11111111-0000-4000-8000-000000000002', 'python',     3, 2, 1.0, 'test'),
    ('11111111-0000-4000-8000-000000000002', 'fastapi',    3, NULL, 1.0, 'self'),
    ('11111111-0000-4000-8000-000000000002', 'postgresql', 2, NULL, 0.5, 'self'),
    ('11111111-0000-4000-8000-000000000003', 'typescript', 4, 4, 3.0, 'test'),
    ('11111111-0000-4000-8000-000000000003', 'react',      4, 4, 3.0, 'test'),
    ('11111111-0000-4000-8000-000000000004', 'python',     5, 5, 5.0, 'test'),
    ('11111111-0000-4000-8000-000000000004', 'pytorch',    4, 4, 4.0, 'test'),
    ('11111111-0000-4000-8000-000000000004', 'transformers', 4, NULL, 3.0, 'github'),
    ('11111111-0000-4000-8000-000000000004', 'rag',        4, NULL, 2.0, 'self'),
    ('11111111-0000-4000-8000-000000000005', 'kubernetes', 5, 5, 6.0, 'test'),
    ('11111111-0000-4000-8000-000000000005', 'terraform',  4, NULL, 4.0, 'self'),
    ('11111111-0000-4000-8000-000000000005', 'mentoring',  4, NULL, 5.0, 'self'),
    ('11111111-0000-4000-8000-000000000006', 'pytest',     3, 2, 0.5, 'test'),
    ('11111111-0000-4000-8000-000000000006', 'test_design', 3, 2, 0.5, 'test')
) AS m(candidate_id, skill_code, self_level, confirmed_level, years, source)
JOIN ref.skills s ON s.code = m.skill_code;

INSERT INTO profile.candidate_team_roles (candidate_id, team_role_id)
SELECT m.candidate_id::uuid, r.id
FROM (VALUES
    ('11111111-0000-4000-8000-000000000001', 'developer'),
    ('11111111-0000-4000-8000-000000000001', 'reviewer'),
    ('11111111-0000-4000-8000-000000000004', 'researcher'),
    ('11111111-0000-4000-8000-000000000005', 'tech_lead'),
    ('11111111-0000-4000-8000-000000000005', 'mentor')
) AS m(candidate_id, role_code)
JOIN ref.team_roles r ON r.code = m.role_code;

-- EAV-значения: тип значения гарантирован составным FK на (id, data_type)
INSERT INTO profile.candidate_attribute_values (candidate_id, attribute_id, data_type, option_id)
SELECT '11111111-0000-4000-8000-000000000001', d.id, d.data_type, o.id
FROM ref.attribute_defs d
JOIN ref.attribute_options o ON o.attribute_id = d.id AND o.code = 'c1'
WHERE d.scope = 'candidate' AND d.code = 'english_level';

INSERT INTO profile.candidate_attribute_values (candidate_id, attribute_id, data_type, value_boolean)
SELECT '11111111-0000-4000-8000-000000000001', d.id, d.data_type, true
FROM ref.attribute_defs d WHERE d.scope = 'candidate' AND d.code = 'business_trips';

INSERT INTO profile.candidate_attribute_values (candidate_id, attribute_id, data_type, value_integer)
SELECT '11111111-0000-4000-8000-000000000004', d.id, d.data_type, 6
FROM ref.attribute_defs d WHERE d.scope = 'candidate' AND d.code = 'preferred_team_size';

-- Резюме: загруженный PDF и сгенерированный платформой профиль
INSERT INTO profile.resume_documents (id, candidate_id, kind, storage_key, file_name, size_bytes, parse_status, raw_text, parsed_at) VALUES
    ('dddddddd-0000-4000-8000-000000000001', '11111111-0000-4000-8000-000000000001', 'uploaded',
     'resumes/11111111/kovaleva.pdf', 'kovaleva.pdf', 184320, 'done',
     'Ковалёва Анна Сергеевна. Backend-разработчик. Python, FastAPI, PostgreSQL, Docker. Опыт 4.5 года.', now() - interval '58 days'),
    ('dddddddd-0000-4000-8000-000000000002', '11111111-0000-4000-8000-000000000001', 'generated',
     'resumes/11111111/profile-generated.pdf', 'profile.pdf', 96000, 'done', NULL, now() - interval '3 days');

INSERT INTO profile.resume_parse_results (document_id, model, payload, confidence, applied_at) VALUES
    ('dddddddd-0000-4000-8000-000000000001', 'demo-extractor-v1',
     '{"full_name":"Ковалёва Анна Сергеевна","experience_months":54,"skills":["python","fastapi","postgresql","docker"],"city":"Москва"}'::jsonb,
     0.92, now() - interval '58 days');

-- --- ФСП ---------------------------------------------------------------------
-- Строка есть у каждого кандидата: отсутствие истории — это статус, а не NULL-дыра
INSERT INTO profile.fsp_profiles (candidate_id, fsp_participant_id, verification_status, linked_at, synced_at) VALUES
    ('11111111-0000-4000-8000-000000000001', 'FSP-0000123', 'verified', now() - interval '55 days', now() - interval '4 days'),
    ('11111111-0000-4000-8000-000000000004', 'FSP-0000456', 'verified', now() - interval '28 days', now() - interval '4 days'),
    ('11111111-0000-4000-8000-000000000005', 'FSP-0000789', 'pending',  now() - interval '5 days',  NULL);
INSERT INTO profile.fsp_profiles (candidate_id, verification_status)
SELECT user_id, 'unlinked' FROM profile.candidates
WHERE user_id NOT IN (SELECT candidate_id FROM profile.fsp_profiles);

INSERT INTO profile.fsp_events (external_id, title, discipline, level, starts_on, ends_on) VALUES
    ('FSP-EV-2025-01', 'Всероссийские соревнования по спортивному программированию', 'Продуктовое программирование', 'federal',       DATE '2025-04-12', DATE '2025-04-14'),
    ('FSP-EV-2025-02', 'Региональный этап: алгоритмическое программирование',        'Алгоритмическое программирование', 'regional', DATE '2025-09-20', DATE '2025-09-21'),
    ('FSP-EV-2024-07', 'Хакатон по программированию систем ИИ',                      'Программирование систем ИИ', 'federal',       DATE '2024-11-08', DATE '2024-11-10');

INSERT INTO profile.fsp_achievements (candidate_id, event_id, place, participants, team_name, score, source, confirmed_at)
SELECT m.candidate_id::uuid, e.id, m.place, m.participants, m.team_name, m.score, 'registry', now() - interval '20 days'
FROM (VALUES
    ('11111111-0000-4000-8000-000000000001', 'FSP-EV-2025-01', 2,  180, 'Ctrl+Alt+Win', 87.5),
    ('11111111-0000-4000-8000-000000000001', 'FSP-EV-2024-07', 5,  120, 'Ctrl+Alt+Win', 71.0),
    ('11111111-0000-4000-8000-000000000004', 'FSP-EV-2024-07', 1,  120, 'Vector Shift', 94.0),
    ('11111111-0000-4000-8000-000000000004', 'FSP-EV-2025-02', 3,   64, 'Vector Shift', 80.5)
) AS m(candidate_id, event_code, place, participants, team_name, score)
JOIN profile.fsp_events e ON e.external_id = m.event_code;

-- --- GitHub ------------------------------------------------------------------
INSERT INTO profile.github_accounts (candidate_id, github_user_id, login, linked_at, indexed_at) VALUES
    ('11111111-0000-4000-8000-000000000001', 100001, 'anna-kovaleva-demo', now() - interval '50 days', now() - interval '3 days'),
    ('11111111-0000-4000-8000-000000000004', 100004, 'denis-orlov-demo',   now() - interval '27 days', now() - interval '2 days');

INSERT INTO profile.github_repos (candidate_id, github_repo_id, full_name, description, stars, forks, commits_by_owner, primary_language, pushed_at, indexed_at) VALUES
    ('11111111-0000-4000-8000-000000000001', 900001, 'anna-kovaleva-demo/order-service', 'Сервис заказов на FastAPI', 34, 6, 214, 'Python', now() - interval '12 days', now() - interval '3 days'),
    ('11111111-0000-4000-8000-000000000001', 900002, 'anna-kovaleva-demo/pg-playground', 'Эксперименты с планами запросов', 8, 1, 57, 'Python', now() - interval '40 days', now() - interval '3 days'),
    ('11111111-0000-4000-8000-000000000004', 900003, 'denis-orlov-demo/rag-bench',       'Бенчмарк RAG-пайплайнов', 120, 19, 311, 'Python', now() - interval '5 days', now() - interval '2 days');

INSERT INTO profile.github_repo_languages (repo_id, language, bytes)
SELECT r.id, l.language, l.bytes
FROM profile.github_repos r
JOIN (VALUES
    (900001, 'Python', 412000), (900001, 'Dockerfile', 2100),
    (900002, 'Python', 88000),  (900002, 'PLpgSQL',   15000),
    (900003, 'Python', 760000), (900003, 'Jupyter Notebook', 230000)
) AS l(repo_id, language, bytes) ON l.repo_id = r.github_repo_id;

INSERT INTO profile.github_signals (candidate_id, skill_id, score, evidence)
SELECT m.candidate_id::uuid, s.id, m.score, m.evidence::jsonb
FROM (VALUES
    ('11111111-0000-4000-8000-000000000001', 'python', 78.0, '{"repos":2,"bytes":500000,"recent_push_days":12}'),
    ('11111111-0000-4000-8000-000000000001', 'docker', 45.0, '{"repos":1,"dockerfiles":1}'),
    ('11111111-0000-4000-8000-000000000004', 'python', 91.0, '{"repos":1,"bytes":760000,"stars":120}'),
    ('11111111-0000-4000-8000-000000000004', 'transformers', 69.0, '{"imports":37}')
) AS m(candidate_id, skill_code, score, evidence)
JOIN ref.skills s ON s.code = m.skill_code;

-- =============================================================================
--  Опрос -> тест -> категория (сквозной сценарий для Анны Ковалёвой)
-- =============================================================================

INSERT INTO assess.survey_templates (industry_id, code, title)
SELECT id, 'it_intro_v1', 'Входной опрос: отрасль и специализация' FROM ref.industries WHERE code = 'it';

INSERT INTO assess.survey_questions (survey_id, order_no, format, text)
SELECT s.id, q.order_no, q.format::assess.question_format, q.text
FROM assess.survey_templates s
CROSS JOIN (VALUES
    (1, 'single_choice', 'Чем вы занимаетесь большую часть рабочего времени?'),
    (2, 'multi_choice',  'С какими инструментами работали в последний год?'),
    (3, 'single_choice', 'На какой грейд вы претендуете?')
) AS q(order_no, format, text)
WHERE s.code = 'it_intro_v1';

INSERT INTO assess.survey_options (question_id, order_no, label, specialization_id, skill_id, weight)
SELECT q.id, o.order_no, o.label,
       (SELECT id FROM ref.specializations WHERE code = o.spec_code),
       (SELECT id FROM ref.skills WHERE code = o.skill_code),
       o.weight
FROM assess.survey_questions q
JOIN assess.survey_templates s ON s.id = q.survey_id AND s.code = 'it_intro_v1'
JOIN (VALUES
    (1, 1, 'Серверная логика и API',          'backend',     NULL,         1.00),
    (1, 2, 'Интерфейсы в браузере',           'frontend',    NULL,         1.00),
    (1, 3, 'Модели и данные',                 'ml_engineer', NULL,         1.00),
    (1, 4, 'Инфраструктура и релизы',         'devops',      NULL,         1.00),
    (2, 1, 'PostgreSQL',                      NULL,          'postgresql', 1.00),
    (2, 2, 'Docker',                          NULL,          'docker',     0.80),
    (2, 3, 'Kubernetes',                      NULL,          'kubernetes', 0.80),
    (3, 1, 'Junior',                          NULL,          NULL,         1.00),
    (3, 2, 'Middle',                          NULL,          NULL,         1.00),
    (3, 3, 'Senior',                          NULL,          NULL,         1.00)
) AS o(question_no, order_no, label, spec_code, skill_code, weight) ON o.question_no = q.order_no;

INSERT INTO assess.survey_sessions (id, candidate_id, survey_id, status, specialization_id, claimed_grade_id, started_at, finished_at)
SELECT '66666666-0000-4000-8000-000000000001', '11111111-0000-4000-8000-000000000001', s.id, 'scored',
       (SELECT id FROM ref.specializations WHERE code = 'backend'),
       (SELECT g.id FROM ref.grades g JOIN ref.industries i ON i.id = g.industry_id WHERE g.code = 'middle' AND i.code = 'it'),
       now() - interval '59 days', now() - interval '59 days' + interval '4 minutes'
FROM assess.survey_templates s WHERE s.code = 'it_intro_v1';

INSERT INTO assess.survey_answers (session_id, question_id, option_id)
SELECT '66666666-0000-4000-8000-000000000001', o.question_id, o.id
FROM assess.survey_options o
JOIN assess.survey_questions q ON q.id = o.question_id
JOIN assess.survey_templates s ON s.id = q.survey_id AND s.code = 'it_intro_v1'
WHERE (q.order_no, o.order_no) IN ((1, 1), (2, 1), (2, 2), (3, 2));

-- Экземпляры заданий: сгенерированы под конкретного кандидата и больше нигде не используются
INSERT INTO assess.tasks (id, template_id, candidate_id, statement, answer_key, difficulty, max_score, generated_by, generation_seed)
SELECT t.id::uuid, tt.id, '11111111-0000-4000-8000-000000000001', t.statement, t.answer_key::jsonb,
       tt.difficulty, tt.max_score, 'demo-generator-v1', t.seed
FROM assess.task_templates tt
JOIN (VALUES
    ('be_mid_api_design', '44444444-0000-4000-8000-000000000001',
     'Сервис доставки продуктов. Спроектируйте REST-ресурс для заказа: создание, частичное изменение состава, отмена. Укажите пути, методы, коды ответов, поведение при повторной отправке запроса и способ постраничной выдачи истории заказов.',
     '{"must_cover":["idempotency","pagination","status_codes","errors"]}', 10241),
    ('be_mid_schema_fix', '44444444-0000-4000-8000-000000000002',
     'Ниже DDL складского учёта. Найдите дефекты проектирования и предложите исправления: CREATE TABLE items (id serial, name text, qty text); CREATE TABLE moves (id serial, item_id int, delta text, created text);',
     '{"defects":["no_fk","wrong_types","no_pk_constraint","no_index"]}', 10242)
) AS t(template_code, id, statement, answer_key, seed) ON t.template_code = tt.code;

INSERT INTO assess.test_sessions (
    id, candidate_id, survey_session_id, specialization_id, claimed_grade_id,
    status, outcome, assigned_category_id, total_score, max_score,
    started_at, deadline_at, finished_at, created_at
)
VALUES (
    '55555555-0000-4000-8000-000000000001',
    '11111111-0000-4000-8000-000000000001',
    '66666666-0000-4000-8000-000000000001',
    (SELECT id FROM ref.specializations WHERE code = 'backend'),
    (SELECT g.id FROM ref.grades g JOIN ref.industries i ON i.id = g.industry_id WHERE g.code = 'middle' AND i.code = 'it'),
    'scored', 'passed',
    (SELECT id FROM ref.categories WHERE slug = 'backend.middle'),
    18.50, 22.00,
    now() - interval '59 days', now() - interval '59 days' + interval '90 minutes',
    now() - interval '59 days' + interval '63 minutes', now() - interval '59 days'
);

INSERT INTO assess.test_session_tasks (session_id, task_id, order_no, weight) VALUES
    ('55555555-0000-4000-8000-000000000001', '44444444-0000-4000-8000-000000000001', 1, 1.00),
    ('55555555-0000-4000-8000-000000000001', '44444444-0000-4000-8000-000000000002', 2, 1.00);

INSERT INTO assess.task_answers (
    session_id, task_id, answer_text, score, max_score, checked_by, feedback,
    submitted_at, checked_at, time_spent_sec, paste_count, focus_lost_count
) VALUES
    ('55555555-0000-4000-8000-000000000001', '44444444-0000-4000-8000-000000000001',
     'POST /orders, PATCH /orders/{id}/items, POST /orders/{id}/cancel. Повтор защищён ключом Idempotency-Key, история — курсорная пагинация.',
     8.50, 10.00, 'llm', 'Идемпотентность и пагинация раскрыты, не хватает версионирования API.',
     now() - interval '59 days' + interval '28 minutes', now() - interval '59 days' + interval '70 minutes', 1620, 0, 1),
    ('55555555-0000-4000-8000-000000000001', '44444444-0000-4000-8000-000000000002',
     'qty и delta должны быть целочисленными, created — timestamptz, нужен FK moves.item_id -> items.id и индекс по нему, явный PRIMARY KEY.',
     10.00, 12.00, 'llm', 'Найдены все дефекты, исправления корректны.',
     now() - interval '59 days' + interval '58 minutes', now() - interval '59 days' + interval '70 minutes', 1740, 0, 0);

-- История грейдов. У Анны источник — тест, у остальных демо-данные заведены импортом.
INSERT INTO assess.grade_assignments (candidate_id, category_id, source, test_session_id, assigned_at, next_change_allowed_at, is_current)
VALUES (
    '11111111-0000-4000-8000-000000000001',
    (SELECT id FROM ref.categories WHERE slug = 'backend.middle'),
    'test', '55555555-0000-4000-8000-000000000001',
    now() - interval '59 days', now() - interval '59 days' + interval '90 days', true
);

INSERT INTO assess.grade_assignments (candidate_id, category_id, source, assigned_at, next_change_allowed_at, is_current, note)
SELECT c.user_id, c.current_category_id, 'import', now() - interval '30 days', now() - interval '30 days' + interval '90 days', true,
       'Демо-данные'
FROM profile.candidates c
WHERE c.user_id <> '11111111-0000-4000-8000-000000000001';

-- =============================================================================
--  Работодатели, вакансии, приглашения
-- =============================================================================

INSERT INTO hire.companies (id, name, legal_name, inn, industry_id, description, website, size, city, is_verified) VALUES
    ('33333333-0000-4000-8000-000000000001', 'ТехноЛайн', 'ООО «ТехноЛайн»', '7701234567',
     (SELECT id FROM ref.industries WHERE code = 'it'),
     'Продуктовая компания: сервисы для логистики, 120 человек в разработке.', 'https://technoline.example', 'medium', 'Москва', true),
    ('33333333-0000-4000-8000-000000000002', 'Датум', 'АО «Датум»', '7809876543',
     (SELECT id FROM ref.industries WHERE code = 'it'),
     'Платформа аналитики и ML-сервисов для ритейла.', 'https://datum.example', 'small', 'Санкт-Петербург', false);

INSERT INTO hire.employers (user_id, company_id, full_name, position, contact_email, contact_phone, is_owner) VALUES
    ('22222222-0000-4000-8000-000000000001', '33333333-0000-4000-8000-000000000001', 'Светлана Жукова', 'Руководитель подбора', 'hr@technoline.example', '+74951234567', true),
    ('22222222-0000-4000-8000-000000000002', '33333333-0000-4000-8000-000000000002', 'Артём Белов',    'Технический директор', 'lead@datum.example',  '+78121234567', true);

INSERT INTO hire.vacancies (
    id, company_id, author_user_id, title, description, category_id,
    work_format, employment_type, city, salary_min, salary_max, status, published_at
) VALUES
    ('77777777-0000-4000-8000-000000000001', '33333333-0000-4000-8000-000000000001', '22222222-0000-4000-8000-000000000001',
     'Backend-разработчик (Python)',
     'Команда логистической платформы: сервисы маршрутизации и расчёта стоимости. Стек: Python, FastAPI, PostgreSQL, Kubernetes. Нужен человек, который умеет проектировать схему данных и держать нагрузку.',
     (SELECT id FROM ref.categories WHERE slug = 'backend.middle'), 'hybrid', 'full_time', 'Москва', 240000, 320000, 'published', now() - interval '14 days'),

    ('77777777-0000-4000-8000-000000000002', '33333333-0000-4000-8000-000000000002', '22222222-0000-4000-8000-000000000002',
     'ML-инженер (NLP)',
     'Поиск и ранжирование товарных карточек: эмбеддинги, retrieval, оценка качества выдачи. Нужен опыт вывода моделей в прод.',
     (SELECT id FROM ref.categories WHERE slug = 'ml_engineer.middle'), 'remote', 'full_time', NULL, 280000, 380000, 'published', now() - interval '9 days'),

    ('77777777-0000-4000-8000-000000000003', '33333333-0000-4000-8000-000000000001', '22222222-0000-4000-8000-000000000001',
     'Frontend-разработчик (React)',
     'Личный кабинет клиента: React, TypeScript, дизайн-система, доступность.',
     (SELECT id FROM ref.categories WHERE slug = 'frontend.middle'), 'remote', 'full_time', NULL, 210000, 270000, 'published', now() - interval '7 days');

INSERT INTO hire.vacancy_skills (vacancy_id, skill_id, importance, min_level)
SELECT m.vacancy_id::uuid, s.id, m.importance::hire.skill_importance, m.min_level
FROM (VALUES
    ('77777777-0000-4000-8000-000000000001', 'python',     'required',     4),
    ('77777777-0000-4000-8000-000000000001', 'postgresql', 'required',     3),
    ('77777777-0000-4000-8000-000000000001', 'fastapi',    'nice_to_have', 2),
    ('77777777-0000-4000-8000-000000000001', 'kubernetes', 'nice_to_have', 2),
    ('77777777-0000-4000-8000-000000000002', 'python',     'required',     4),
    ('77777777-0000-4000-8000-000000000002', 'pytorch',    'required',     3),
    ('77777777-0000-4000-8000-000000000002', 'rag',        'nice_to_have', 2),
    ('77777777-0000-4000-8000-000000000003', 'typescript', 'required',     3),
    ('77777777-0000-4000-8000-000000000003', 'react',      'required',     3)
) AS m(vacancy_id, skill_code, importance, min_level)
JOIN ref.skills s ON s.code = m.skill_code;

-- Описание потребности: вход конвейера подбора. Существует и без публикации вакансии.
INSERT INTO hire.employer_needs (
    id, company_id, author_user_id, vacancy_id, title, description,
    specialization_id, grade_id, category_id, positive_prompt, negative_prompt, salary_min, salary_max
) VALUES
    ('88888888-0000-4000-8000-000000000001', '33333333-0000-4000-8000-000000000001', '22222222-0000-4000-8000-000000000001',
     '77777777-0000-4000-8000-000000000001',
     'Backend в команду маршрутизации',
     'Команда из шести человек отвечает за расчёт маршрутов и стоимости доставки. Ищем разработчика, который самостоятельно спроектирует схему данных под новый тариф и доведёт сервис до прода.',
     (SELECT id FROM ref.specializations WHERE code = 'backend'),
     (SELECT g.id FROM ref.grades g JOIN ref.industries i ON i.id = g.industry_id WHERE g.code = 'middle' AND i.code = 'it'),
     (SELECT id FROM ref.categories WHERE slug = 'backend.middle'),
     'Опыт проектирования схемы БД, понимание планов запросов, участие в соревнованиях приветствуется',
     'Только фреймворк без понимания данных; отсутствие опыта работы с нагрузкой',
     240000, 320000),

    ('88888888-0000-4000-8000-000000000002', '33333333-0000-4000-8000-000000000002', '22222222-0000-4000-8000-000000000002',
     NULL,
     'ML-инженер для поиска по каталогу',
     'Нужен человек, который выстроит retrieval по товарному каталогу: эмбеддинги, переранжирование, измеримое качество выдачи.',
     (SELECT id FROM ref.specializations WHERE code = 'ml_engineer'),
     (SELECT g.id FROM ref.grades g JOIN ref.industries i ON i.id = g.industry_id WHERE g.code = 'middle' AND i.code = 'it'),
     (SELECT id FROM ref.categories WHERE slug = 'ml_engineer.middle'),
     'Практика RAG, собственные бенчмарки, вывод моделей в прод',
     'Только академические проекты без эксплуатации',
     280000, 400000);

INSERT INTO hire.need_skills (need_id, skill_id, importance, min_level)
SELECT m.need_id::uuid, s.id, m.importance::hire.skill_importance, m.min_level
FROM (VALUES
    ('88888888-0000-4000-8000-000000000001', 'python',     'required',     4),
    ('88888888-0000-4000-8000-000000000001', 'postgresql', 'required',     3),
    ('88888888-0000-4000-8000-000000000001', 'db_design',  'required',     3),
    ('88888888-0000-4000-8000-000000000002', 'pytorch',    'required',     3),
    ('88888888-0000-4000-8000-000000000002', 'rag',        'required',     2)
) AS m(need_id, skill_code, importance, min_level)
JOIN ref.skills s ON s.code = m.skill_code;

-- Приглашения: работодатель выходит на контакт сам, вилка указана обязательно
INSERT INTO hire.invitations (
    id, company_id, sender_user_id, candidate_id, vacancy_id, need_id, message,
    salary_min, salary_max, contact_method, contact_value, status,
    sent_at, viewed_at, responded_at
) VALUES
    ('99999999-0000-4000-8000-000000000001', '33333333-0000-4000-8000-000000000001', '22222222-0000-4000-8000-000000000001',
     '11111111-0000-4000-8000-000000000001', '77777777-0000-4000-8000-000000000001', '88888888-0000-4000-8000-000000000001',
     'Анна, увидели подтверждённый профиль backend middle и призовое место на всероссийских соревнованиях. Предлагаем команду маршрутизации, гибрид в Москве.',
     260000, 320000, 'email', 'hr@technoline.example', 'accepted',
     now() - interval '6 days', now() - interval '6 days' + interval '3 hours', now() - interval '5 days'),

    ('99999999-0000-4000-8000-000000000002', '33333333-0000-4000-8000-000000000001', '22222222-0000-4000-8000-000000000001',
     '11111111-0000-4000-8000-000000000002', '77777777-0000-4000-8000-000000000001', '88888888-0000-4000-8000-000000000001',
     'Павел, рассматриваем junior-позицию в ту же команду с наставником.',
     120000, 160000, 'platform', NULL, 'sent',
     now() - interval '2 days', NULL, NULL),

    ('99999999-0000-4000-8000-000000000003', '33333333-0000-4000-8000-000000000002', '22222222-0000-4000-8000-000000000002',
     '11111111-0000-4000-8000-000000000004', NULL, '88888888-0000-4000-8000-000000000002',
     'Денис, ищем ML-инженера на поиск по каталогу. Приглашение без привязки к опубликованной вакансии.',
     300000, 400000, 'telegram', '@datum_lead', 'viewed',
     now() - interval '3 days', now() - interval '2 days', NULL);

INSERT INTO hire.invitation_events (invitation_id, from_status, to_status, actor_user_id, created_at) VALUES
    ('99999999-0000-4000-8000-000000000001', NULL,     'sent',     '22222222-0000-4000-8000-000000000001', now() - interval '6 days'),
    ('99999999-0000-4000-8000-000000000001', 'sent',   'viewed',   '11111111-0000-4000-8000-000000000001', now() - interval '6 days' + interval '3 hours'),
    ('99999999-0000-4000-8000-000000000001', 'viewed', 'accepted', '11111111-0000-4000-8000-000000000001', now() - interval '5 days'),
    ('99999999-0000-4000-8000-000000000002', NULL,     'sent',     '22222222-0000-4000-8000-000000000001', now() - interval '2 days'),
    ('99999999-0000-4000-8000-000000000003', NULL,     'sent',     '22222222-0000-4000-8000-000000000002', now() - interval '3 days'),
    ('99999999-0000-4000-8000-000000000003', 'sent',   'viewed',   '11111111-0000-4000-8000-000000000004', now() - interval '2 days');

-- Классический отклик кандидата (дополнительный сценарий)
INSERT INTO hire.applications (id, vacancy_id, candidate_id, cover_letter, status, created_at, viewed_at) VALUES
    ('aaaaaaaa-0000-4000-8000-000000000001', '77777777-0000-4000-8000-000000000003', '11111111-0000-4000-8000-000000000003',
     'Собрала дизайн-систему на прошлом месте, интересен личный кабинет с требованиями по доступности.', 'viewed',
     now() - interval '4 days', now() - interval '3 days');

INSERT INTO hire.application_events (application_id, from_status, to_status, actor_user_id, created_at) VALUES
    ('aaaaaaaa-0000-4000-8000-000000000001', NULL,   'sent',   '11111111-0000-4000-8000-000000000003', now() - interval '4 days'),
    ('aaaaaaaa-0000-4000-8000-000000000001', 'sent', 'viewed', '22222222-0000-4000-8000-000000000001', now() - interval '3 days');

-- Контакты раскрыты ровно там, где для этого есть основание:
-- принятое приглашение и самостоятельный отклик. Больше ни у кого.
INSERT INTO hire.contact_disclosures (candidate_id, company_id, reason, invitation_id, application_id, granted_at) VALUES
    ('11111111-0000-4000-8000-000000000001', '33333333-0000-4000-8000-000000000001', 'invitation_accepted', '99999999-0000-4000-8000-000000000001', NULL, now() - interval '5 days'),
    ('11111111-0000-4000-8000-000000000003', '33333333-0000-4000-8000-000000000001', 'application_sent',    NULL, 'aaaaaaaa-0000-4000-8000-000000000001', now() - interval '4 days');

-- Регулярное короткое задание от работодателя: держит профиль свежим
INSERT INTO assess.tasks (id, template_id, candidate_id, statement, difficulty, max_score, generated_by, generation_seed)
SELECT '44444444-0000-4000-8000-000000000003', tt.id, '11111111-0000-4000-8000-000000000001',
       'Коротко: как бы вы организовали хранение тарифов, меняющихся во времени, чтобы исторические заказы считались по тарифу на момент заказа?',
       tt.difficulty, 5.00, 'demo-generator-v1', 10243
FROM assess.task_templates tt WHERE tt.code = 'be_mid_schema_fix';

INSERT INTO assess.regular_assignments (candidate_id, task_id, company_id, status, answer_text, score, max_score, issued_at, due_at, answered_at) VALUES
    ('11111111-0000-4000-8000-000000000001', '44444444-0000-4000-8000-000000000003', '33333333-0000-4000-8000-000000000001',
     'scored', 'Версионирование тарифов интервалами действия, в заказе — ссылка на конкретную версию, а не на тариф.',
     4.50, 5.00, now() - interval '4 days', now() - interval '1 day', now() - interval '3 days');

-- =============================================================================
--  Подбор: эмбеддинги, прогон, объяснения, чат
-- =============================================================================
--  Вектора здесь случайные: они демонстрируют работоспособность индекса и
--  запросов, но не семантику. Реальные значения пишет сервис эмбеддингов.
-- =============================================================================

INSERT INTO match.candidate_embeddings (candidate_id, model, embedding, source_hash)
SELECT c.user_id, 'demo-random-1024',
       (SELECT ('[' || string_agg(round((random() * 2 - 1)::numeric, 6)::text, ',') || ']')::vector
        FROM generate_series(1, 1024)),
       md5(c.user_id::text)
FROM profile.candidates c;

INSERT INTO match.vacancy_embeddings (vacancy_id, model, embedding, source_hash)
SELECT v.id, 'demo-random-1024',
       (SELECT ('[' || string_agg(round((random() * 2 - 1)::numeric, 6)::text, ',') || ']')::vector
        FROM generate_series(1, 1024)),
       md5(v.id::text)
FROM hire.vacancies v;

INSERT INTO match.need_embeddings (need_id, model, embedding, source_hash)
SELECT n.id, 'demo-random-1024',
       (SELECT ('[' || string_agg(round((random() * 2 - 1)::numeric, 6)::text, ',') || ']')::vector
        FROM generate_series(1, 1024)),
       md5(n.id::text)
FROM hire.employer_needs n;

INSERT INTO match.search_runs (id, need_id, author_user_id, parent_run_id, strategy, params, status, pool_size, llm_model, summary, created_at, finished_at) VALUES
    ('bbbbbbbb-0000-4000-8000-000000000001', '88888888-0000-4000-8000-000000000001', '22222222-0000-4000-8000-000000000001',
     NULL, 'prefilter_vector_llm',
     '{"pool":120,"fresh_share":0.2,"random_share":0.1,"vector_top_k":40,"llm_top_k":10,"min_vector_score":0.55}'::jsonb,
     'done', 120, 'demo-llm-v1',
     'В категории backend.middle отобрано 10 кандидатов. Выше остальных — профили с подтверждённым навыком проектирования БД и достижениями ФСП.',
     now() - interval '7 days', now() - interval '7 days' + interval '38 seconds'),

    -- уточнение из чата: отдельный прогон, предыдущая подборка сохранена
    ('bbbbbbbb-0000-4000-8000-000000000002', '88888888-0000-4000-8000-000000000001', '22222222-0000-4000-8000-000000000001',
     'bbbbbbbb-0000-4000-8000-000000000001', 'prefilter_vector_llm',
     '{"pool":120,"vector_top_k":40,"llm_top_k":10,"filters":{"city":"Москва","relocation":false}}'::jsonb,
     'done', 64, 'demo-llm-v1',
     'Сузили до Москвы без релокации: осталось 4 кандидата, порядок сохранён.',
     now() - interval '7 days' + interval '6 minutes', now() - interval '7 days' + interval '6 minutes 31 seconds');

INSERT INTO match.search_results (run_id, candidate_id, stage, rank, vector_score, llm_score, final_score, explanation) VALUES
    ('bbbbbbbb-0000-4000-8000-000000000001', '11111111-0000-4000-8000-000000000001', 'llm', 1, 0.84210, 0.93000, 0.90000,
     'Подтверждённый backend.middle, навык проектирования БД подтверждён тестом (10 из 12), призовое место на всероссийских соревнованиях ФСП. Вилка кандидата пересекается с вилкой потребности.'),
    ('bbbbbbbb-0000-4000-8000-000000000001', '11111111-0000-4000-8000-000000000005', 'llm', 2, 0.71050, 0.62000, 0.65000,
     'Сильный инфраструктурный профиль, но специализация devops, а не backend: подходит как смежный вариант, если расширить потребность.'),
    ('bbbbbbbb-0000-4000-8000-000000000001', '11111111-0000-4000-8000-000000000002', 'llm', 3, 0.66400, 0.41000, 0.47000,
     'Совпадает стек, но грейд junior против требуемого middle и нет подтверждённого навыка проектирования БД.'),
    ('bbbbbbbb-0000-4000-8000-000000000001', '11111111-0000-4000-8000-000000000004', 'vector', 4, 0.58900, NULL, NULL, NULL),
    ('bbbbbbbb-0000-4000-8000-000000000001', '11111111-0000-4000-8000-000000000003', 'prefilter', 5, NULL, NULL, NULL, NULL),

    ('bbbbbbbb-0000-4000-8000-000000000002', '11111111-0000-4000-8000-000000000001', 'llm', 1, 0.84210, 0.93000, 0.91000,
     'Москва, релокация не требуется — условие уточнения выполнено, позиция в выдаче сохранена.'),
    ('bbbbbbbb-0000-4000-8000-000000000002', '11111111-0000-4000-8000-000000000005', 'llm', 2, 0.71050, 0.60000, 0.63000,
     'Москва, офис. Специализация по-прежнему смежная.');

INSERT INTO match.chat_threads (id, need_id, user_id, title, created_at, updated_at) VALUES
    ('cccccccc-0000-4000-8000-000000000001', '88888888-0000-4000-8000-000000000001', '22222222-0000-4000-8000-000000000001',
     'Подборка: backend в команду маршрутизации', now() - interval '7 days', now() - interval '7 days' + interval '7 minutes');

INSERT INTO match.chat_messages (thread_id, run_id, role, content, tokens, created_at) VALUES
    ('cccccccc-0000-4000-8000-000000000001', 'bbbbbbbb-0000-4000-8000-000000000001', 'assistant',
     'Подобрала 10 кандидатов в категории backend.middle. Первый — с подтверждённым навыком проектирования БД и призовым местом ФСП.', 64, now() - interval '7 days'),
    ('cccccccc-0000-4000-8000-000000000001', NULL, 'user',
     'Оставь только Москву и тех, кому не нужна релокация.', 18, now() - interval '7 days' + interval '5 minutes'),
    ('cccccccc-0000-4000-8000-000000000001', 'bbbbbbbb-0000-4000-8000-000000000002', 'assistant',
     'Уточнила: осталось 4 кандидата. Предыдущая подборка сохранена, к ней можно вернуться.', 42, now() - interval '7 days' + interval '6 minutes');

COMMIT;
