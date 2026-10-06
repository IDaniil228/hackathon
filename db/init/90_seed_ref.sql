-- =============================================================================
--  90. Справочники
-- =============================================================================
--  Наполнение таксономии. Вторая отрасль («Маркетинг») заведена намеренно:
--  она показывает, что схема не знает про ИТ — специализации, грейды, навыки и
--  банк заданий подставляются данными, а не кодом.
-- =============================================================================

-- --- Отрасли -----------------------------------------------------------------
INSERT INTO ref.industries (code, name, description) VALUES
    ('it',        'Информационные технологии', 'Разработка ПО, данные, инфраструктура'),
    ('marketing', 'Маркетинг',                 'Демонстрация доменной независимости платформы');

-- --- Грейды (одна лестница на отрасль) ---------------------------------------
INSERT INTO ref.grades (industry_id, code, name, level, min_experience_months, cooldown_days)
SELECT i.id, g.code, g.name, g.level, g.min_exp, 90
FROM ref.industries i
CROSS JOIN (VALUES
    ('intern', 'Стажёр',  0,   0),
    ('junior', 'Junior', 10,   0),
    ('middle', 'Middle', 20,  18),
    ('senior', 'Senior', 30,  48),
    ('lead',   'Lead',   40,  72)
) AS g(code, name, level, min_exp)
WHERE i.code IN ('it', 'marketing');

-- --- Специализации -----------------------------------------------------------
INSERT INTO ref.specializations (industry_id, code, name, description, sort_order)
SELECT i.id, s.code, s.name, s.description, s.sort_order
FROM ref.industries i
CROSS JOIN (VALUES
    ('backend',          'Backend-разработка',      'Серверная логика, API, хранилища',            10),
    ('frontend',         'Frontend-разработка',     'Веб-интерфейсы',                              20),
    ('fullstack',        'Fullstack-разработка',    'Клиент и сервер одним специалистом',          30),
    ('mobile',           'Мобильная разработка',    'iOS, Android, кроссплатформа',                40),
    ('ml_engineer',      'ML-инженерия',            'Модели, инференс, MLOps',                     50),
    ('data_analyst',     'Аналитика данных',        'Витрины, метрики, визуализация',              60),
    ('devops',           'DevOps / SRE',            'CI/CD, инфраструктура, надёжность',           70),
    ('qa',               'Тестирование',            'Ручное и автоматизированное тестирование',    80),
    ('security',         'Информационная безопасность', 'AppSec, инфраструктурная безопасность',   90),
    ('systems_analyst',  'Системный анализ',        'Требования, интеграции, документация',       100)
) AS s(code, name, description, sort_order)
WHERE i.code = 'it';

-- Подобласти: parent_id внутри той же отрасли
INSERT INTO ref.specializations (industry_id, parent_id, code, name, sort_order)
SELECT p.industry_id, p.id, s.code, s.name, s.sort_order
FROM ref.specializations p
JOIN ref.industries i ON i.id = p.industry_id AND i.code = 'it'
JOIN (VALUES
    ('backend',     'backend_python', 'Backend / Python', 11),
    ('backend',     'backend_go',     'Backend / Go',     12),
    ('backend',     'backend_java',   'Backend / Java',   13),
    ('ml_engineer', 'ml_nlp',         'ML / NLP',         51),
    ('ml_engineer', 'ml_cv',          'ML / CV',          52)
) AS s(parent_code, code, name, sort_order) ON s.parent_code = p.code;

INSERT INTO ref.specializations (industry_id, code, name, description, sort_order)
SELECT i.id, s.code, s.name, s.description, s.sort_order
FROM ref.industries i
CROSS JOIN (VALUES
    ('smm',            'SMM',                'Соцсети и сообщества',        10),
    ('perf_marketing', 'Performance-маркетинг', 'Платный трафик и аналитика', 20)
) AS s(code, name, description, sort_order)
WHERE i.code = 'marketing';

-- --- Категории: полное декартово произведение специализаций и грейдов --------
INSERT INTO ref.categories (specialization_id, grade_id, slug)
SELECT s.id, g.id, s.code || '.' || g.code
FROM ref.specializations s
JOIN ref.grades g ON g.industry_id = s.industry_id;

-- --- Области навыков ---------------------------------------------------------
INSERT INTO ref.skill_areas (code, name, sort_order) VALUES
    ('web',     'Web',         10),
    ('ml',      'ML',          20),
    ('devops',  'DevOps',      30),
    ('data',    'Данные',      40),
    ('mobile',  'Мобильная разработка', 50),
    ('qa',      'Тестирование', 60),
    ('soft',    'SoftSkills',  70),
    ('mkt',     'Маркетинг',   80);

INSERT INTO ref.skill_areas (parent_id, code, name, sort_order)
SELECT p.id, a.code, a.name, a.sort_order
FROM ref.skill_areas p
JOIN (VALUES
    ('web', 'web_backend',  'Web / Backend',  11),
    ('web', 'web_frontend', 'Web / Frontend', 12),
    ('ml',  'ml_nlp_area',  'ML / NLP',       21)
) AS a(parent_code, code, name, sort_order) ON a.parent_code = p.code;

-- --- Навыки ------------------------------------------------------------------
INSERT INTO ref.skills (area_id, kind, code, name)
SELECT a.id, s.kind::ref.skill_kind, s.code, s.name
FROM ref.skill_areas a
JOIN (VALUES
    -- языки
    ('web_backend',  'language',  'python',        'Python'),
    ('web_backend',  'language',  'go',            'Go'),
    ('web_backend',  'language',  'java',          'Java'),
    ('web_backend',  'language',  'csharp',        'C#'),
    ('web_backend',  'language',  'php',           'PHP'),
    ('web_frontend', 'language',  'typescript',    'TypeScript'),
    ('web_frontend', 'language',  'javascript',    'JavaScript'),
    ('mobile',       'language',  'kotlin',        'Kotlin'),
    ('mobile',       'language',  'swift',         'Swift'),
    ('ml',           'language',  'r_lang',        'R'),
    ('data',         'language',  'sql',           'SQL'),
    -- фреймворки
    ('web_backend',  'framework', 'fastapi',       'FastAPI'),
    ('web_backend',  'framework', 'django',        'Django'),
    ('web_backend',  'framework', 'spring',        'Spring'),
    ('web_frontend', 'framework', 'react',         'React'),
    ('web_frontend', 'framework', 'vue',           'Vue'),
    ('ml',           'framework', 'pytorch',       'PyTorch'),
    ('ml',           'framework', 'tensorflow',    'TensorFlow'),
    ('ml_nlp_area',  'framework', 'transformers',  'Transformers'),
    ('mobile',       'framework', 'flutter',       'Flutter'),
    -- инструменты
    ('web_backend',  'tool',      'postgresql',    'PostgreSQL'),
    ('web_backend',  'tool',      'redis',         'Redis'),
    ('data',         'tool',      'clickhouse',    'ClickHouse'),
    ('data',         'tool',      'airflow',       'Apache Airflow'),
    ('devops',       'tool',      'docker',        'Docker'),
    ('devops',       'tool',      'kubernetes',    'Kubernetes'),
    ('devops',       'tool',      'gitlab_ci',     'GitLab CI'),
    ('devops',       'tool',      'terraform',     'Terraform'),
    ('devops',       'tool',      'prometheus',    'Prometheus'),
    ('qa',           'tool',      'pytest',        'pytest'),
    ('qa',           'tool',      'playwright',    'Playwright'),
    ('ml',           'tool',      'mlflow',        'MLflow'),
    ('ml_nlp_area',  'tool',      'pgvector_skill','pgvector'),
    -- практики
    ('web_backend',  'practice',  'rest_api',      'Проектирование REST API'),
    ('web_backend',  'practice',  'db_design',     'Проектирование БД'),
    ('devops',       'practice',  'ci_cd',         'CI/CD'),
    ('qa',           'practice',  'test_design',   'Тест-дизайн'),
    ('ml',           'practice',  'rag',           'RAG'),
    ('ml',           'practice',  'prompting',     'Промт-инжиниринг'),
    -- soft
    ('soft',         'soft',      'communication', 'Коммуникация'),
    ('soft',         'soft',      'teamwork',      'Работа в команде'),
    ('soft',         'soft',      'mentoring',     'Менторство'),
    ('soft',         'soft',      'ownership',     'Самостоятельность'),
    ('soft',         'soft',      'eng_english',   'Технический английский'),
    -- другая отрасль
    ('mkt',          'tool',      'yandex_direct', 'Яндекс.Директ'),
    ('mkt',          'practice',  'content_plan',  'Контент-планирование')
) AS s(area_code, kind, code, name) ON s.area_code = a.code;

-- Синонимы для разбора резюме и ответов LLM
INSERT INTO ref.skill_aliases (skill_id, alias)
SELECT sk.id, a.alias
FROM ref.skills sk
JOIN (VALUES
    ('python',     'питон'),
    ('python',     'py'),
    ('postgresql', 'Postgres'),
    ('postgresql', 'постгрес'),
    ('postgresql', 'psql'),
    ('javascript', 'JS'),
    ('typescript', 'TS'),
    ('kubernetes', 'k8s'),
    ('docker',     'докер'),
    ('fastapi',    'фастапи'),
    ('rest_api',   'REST'),
    ('ci_cd',      'CICD')
) AS a(skill_code, alias) ON a.skill_code = sk.code;

-- Веса навыков в специализации — вход префильтра подборки
INSERT INTO ref.specialization_skills (specialization_id, skill_id, weight, is_core)
SELECT sp.id, sk.id, m.weight, m.is_core
FROM ref.specializations sp
JOIN ref.industries i ON i.id = sp.industry_id AND i.code = 'it'
JOIN (VALUES
    ('backend',     'python',     1.00, true),
    ('backend',     'postgresql', 0.90, true),
    ('backend',     'rest_api',   0.80, true),
    ('backend',     'docker',     0.60, false),
    ('backend',     'redis',      0.40, false),
    ('frontend',    'typescript', 1.00, true),
    ('frontend',    'react',      0.90, true),
    ('frontend',    'javascript', 0.70, false),
    ('ml_engineer', 'python',     1.00, true),
    ('ml_engineer', 'pytorch',    0.90, true),
    ('ml_engineer', 'transformers', 0.70, false),
    ('ml_engineer', 'rag',        0.60, false),
    ('devops',      'kubernetes', 1.00, true),
    ('devops',      'docker',     0.90, true),
    ('devops',      'terraform',  0.70, false),
    ('devops',      'ci_cd',      0.80, true),
    ('qa',          'pytest',     0.80, true),
    ('qa',          'test_design', 1.00, true)
) AS m(spec_code, skill_code, weight, is_core) ON m.spec_code = sp.code
JOIN ref.skills sk ON sk.code = m.skill_code;

-- --- Роли в команде ----------------------------------------------------------
INSERT INTO ref.team_roles (code, name) VALUES
    ('developer',  'Разработчик'),
    ('tech_lead',  'Технический лидер'),
    ('architect',  'Архитектор'),
    ('mentor',     'Наставник'),
    ('researcher', 'Исследователь'),
    ('reviewer',   'Ревьюер');

-- --- EAV: атрибуты, которых нет в фиксированных колонках ---------------------
INSERT INTO ref.attribute_defs (scope, industry_id, code, name, data_type, unit, is_required, is_public, sort_order) VALUES
    ('candidate', NULL, 'english_level',        'Уровень английского',            'option',  NULL,      false, true,  10),
    ('candidate', NULL, 'available_from',       'Готов приступить с',             'date',    NULL,      false, true,  20),
    ('candidate', NULL, 'business_trips',       'Готовность к командировкам',     'boolean', NULL,      false, true,  30),
    ('candidate', NULL, 'preferred_team_size',  'Комфортный размер команды',      'integer', 'человек', false, false, 40),
    ('candidate', NULL, 'oss_contributions',    'Вклад в open source',            'text',    NULL,      false, true,  50),
    ('vacancy',   NULL, 'has_probation',        'Испытательный срок',             'boolean', NULL,      false, true,  10);

-- Отраслевой атрибут: существует только для маркетинга и подтверждает,
-- что расширение в другую нишу — это вставка строк, а не ALTER TABLE
INSERT INTO ref.attribute_defs (scope, industry_id, code, name, data_type, unit, sort_order)
SELECT 'candidate', i.id, 'portfolio_cases', 'Кейсов в портфолио', 'integer', 'шт', 60
FROM ref.industries i WHERE i.code = 'marketing';

INSERT INTO ref.attribute_options (attribute_id, code, label, sort_order)
SELECT d.id, o.code, o.label, o.sort_order
FROM ref.attribute_defs d
CROSS JOIN (VALUES
    ('a1', 'A1 — Beginner',     10),
    ('a2', 'A2 — Elementary',   20),
    ('b1', 'B1 — Intermediate', 30),
    ('b2', 'B2 — Upper-Int.',   40),
    ('c1', 'C1 — Advanced',     50),
    ('c2', 'C2 — Proficient',   60)
) AS o(code, label, sort_order)
WHERE d.scope = 'candidate' AND d.code = 'english_level';
