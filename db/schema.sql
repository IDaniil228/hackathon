-- =============================================================================
--  Платформа ФСП — схема базы данных
--  PostgreSQL 18 + pgvector
-- =============================================================================
--  Файл самодостаточен и применим повторно: он сносит свои схемы и создаёт их
--  заново вместе со справочниками. Всё выполняется одной транзакцией, поэтому
--  при любой ошибке база остаётся ровно в том состоянии, в котором была.
--
--  Применение к поднятому контейнеру:
--      docker compose exec -T postgres psql -U fsp -d fsp < db/schema.sql
--  либо с хоста:
--      psql "postgresql://fsp:...@localhost:5432/fsp" -f db/schema.sql
--
--  Демонстрационные данные — отдельным файлом: db/demo_data.sql
--
--  Содержание:
--      1. Расширения
--      2. Схемы
--      3. core    — пользователи, токены, сессии, согласия 152-ФЗ
--      4. ref     — отрасли, специализации, грейды, навыки, описание EAV
--      5. profile — анкета кандидата, резюме, ФСП, GitHub
--      6. assess  — опрос, банк заданий, тестирование, история грейдов
--      7. hire    — компании, вакансии, потребности, приглашения, отклики
--      8. match   — эмбеддинги, подборки, объяснимость выдачи, чат
--      9. Индексы
--     10. Справочные данные
--     11. Банк шаблонов заданий
--     12. Настройка подключения
-- =============================================================================

BEGIN;

-- DROP ... CASCADE перечисляет в NOTICE каждый снесённый объект — на 62 таблицах
-- это под сотню строк шума. LOCAL: действует только внутри этой транзакции.
SET LOCAL client_min_messages = warning;

-- -----------------------------------------------------------------------------
--  Снос предыдущей версии схемы
-- -----------------------------------------------------------------------------
--  Порядок обратный созданию, но при CASCADE он не важен. Расширения живут в
--  public и этот снос переживают.
-- -----------------------------------------------------------------------------
DROP SCHEMA IF EXISTS match, hire, assess, profile, ref, core CASCADE;

-- =============================================================================
--  00. Расширения
-- =============================================================================
--  vector  — хранение и поиск эмбеддингов (конвейер подбора: префильтр -> вектор -> LLM)
--  citext  — регистронезависимые email и синонимы навыков без функциональных индексов
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS citext;

-- pg_trgm нужен для поиска по подстроке в названиях навыков/компаний (автодополнение)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- =============================================================================
--  01. Схемы
-- =============================================================================
--  core    — пользователи, аутентификация, согласия 152-ФЗ
--  ref     — справочники предметной области и описание EAV-атрибутов
--  profile — кандидат: анкета, резюме, достижения ФСП, сигналы GitHub
--  assess  — опрос, банк заданий, тестирование, история грейдов
--  hire    — работодатель: компании, вакансии, приглашения, отклики
--  match   — эмбеддинги, подборки, объяснимость выдачи, чат с LLM
--
--  Разделение по схемам повторяет компонентную архитектуру решения: каждый модуль
--  бэкенда работает со своей схемой, граница видна в DDL, а не только в соглашении
--  об именах. Все ENUM-типы объявлены в схеме своего модуля и используются
--  квалифицированно (core.user_status, hire.invitation_status, ...).
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS ref;
CREATE SCHEMA IF NOT EXISTS profile;
CREATE SCHEMA IF NOT EXISTS assess;
CREATE SCHEMA IF NOT EXISTS hire;
CREATE SCHEMA IF NOT EXISTS match;

COMMENT ON SCHEMA core    IS 'Пользователи, сессии, согласия на обработку ПД';
COMMENT ON SCHEMA ref     IS 'Справочники: отрасли, специализации, грейды, навыки, EAV-атрибуты';
COMMENT ON SCHEMA profile IS 'Профиль кандидата: анкета, резюме, ФСП, GitHub';
COMMENT ON SCHEMA assess  IS 'Опрос, банк и генерация заданий, тестирование, присвоение грейда';
COMMENT ON SCHEMA hire    IS 'Компании, вакансии, потребности, приглашения, отклики';
COMMENT ON SCHEMA match   IS 'Векторный поиск, подборки кандидатов, объяснения, чат';

-- Никто, кроме владельца БД, не должен класть объекты в public
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- =============================================================================
--  10. core — пользователи, аутентификация, согласия
-- =============================================================================
--  Регистрация выполняется по e-mail с подтверждением адреса (п. 2.2.4 ТЗ).
--  Поле users.external_subject заранее рассчитано на ФСП ID поверх Keycloak:
--  когда появится реальный IdP, достаточно заполнить subject, а не мигрировать
--  схему. Пароль при этом может отсутствовать — такой пользователь приходит
--  только через внешнего провайдера.
-- =============================================================================

CREATE TYPE core.user_status   AS ENUM ('pending', 'active', 'blocked');
CREATE TYPE core.user_role     AS ENUM ('candidate', 'employer', 'admin');
CREATE TYPE core.token_purpose AS ENUM ('verify_email', 'reset_password');
CREATE TYPE core.consent_kind  AS ENUM ('pd_processing', 'profile_publication', 'fsp_data_sync');

-- -----------------------------------------------------------------------------
CREATE TABLE core.users (
    id                uuid PRIMARY KEY DEFAULT uuidv7(),
    email             citext NOT NULL UNIQUE
                          CONSTRAINT users_email_len CHECK (char_length(email) BETWEEN 5 AND 254)
                          CONSTRAINT users_email_format CHECK (email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[A-Za-z]{2,}$'),
    -- argon2id укладывается в ~100 символов, bcrypt в 60; 255 с запасом на смену алгоритма
    password_hash     varchar(255),
    status            core.user_status NOT NULL DEFAULT 'pending',
    email_verified_at timestamptz,
    -- sub из внешнего IdP (ФСП ID / Keycloak)
    external_subject  varchar(64) UNIQUE,
    last_login_at     timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT users_auth_method_present
        CHECK (password_hash IS NOT NULL OR external_subject IS NOT NULL),
    CONSTRAINT users_active_requires_verified_email
        CHECK (status <> 'active' OR email_verified_at IS NOT NULL)
);

COMMENT ON COLUMN core.users.external_subject IS 'subject внешнего IdP (ФСП ID на базе Keycloak); NULL, если вход по паролю';
COMMENT ON CONSTRAINT users_active_requires_verified_email ON core.users IS 'Активировать аккаунт можно только после подтверждения адреса (п. 2.2.4 ТЗ)';

-- Один человек может быть одновременно и кандидатом, и работодателем,
-- поэтому роль вынесена в отдельную таблицу, а не в колонку users.
CREATE TABLE core.user_roles (
    user_id    uuid NOT NULL REFERENCES core.users (id) ON DELETE CASCADE,
    role       core.user_role NOT NULL,
    granted_at timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (user_id, role)
);

-- -----------------------------------------------------------------------------
-- Одноразовые токены подтверждения e-mail и сброса пароля.
-- Хранится SHA-256 от токена, а не сам токен: утечка таблицы не даёт входа.
CREATE TABLE core.email_tokens (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id    uuid NOT NULL REFERENCES core.users (id) ON DELETE CASCADE,
    purpose    core.token_purpose NOT NULL,
    token_hash varchar(64) NOT NULL UNIQUE
                   CONSTRAINT email_tokens_hash_format CHECK (token_hash ~ '^[a-f0-9]{64}$'),
    expires_at timestamptz NOT NULL,
    used_at    timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT email_tokens_expires_after_create CHECK (expires_at > created_at),
    CONSTRAINT email_tokens_used_after_create    CHECK (used_at IS NULL OR used_at >= created_at)
);

-- Сессии refresh-токенов: разлогинивание и отзыв по устройствам
CREATE TABLE core.refresh_sessions (
    id         uuid PRIMARY KEY DEFAULT uuidv7(),
    user_id    uuid NOT NULL REFERENCES core.users (id) ON DELETE CASCADE,
    token_hash varchar(64) NOT NULL UNIQUE
                   CONSTRAINT refresh_sessions_hash_format CHECK (token_hash ~ '^[a-f0-9]{64}$'),
    user_agent varchar(255),
    ip         inet,
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT refresh_sessions_expires_after_create CHECK (expires_at > created_at)
);

-- -----------------------------------------------------------------------------
-- Согласия по 152-ФЗ. Таблица append-only: отзыв согласия — это не DELETE,
-- а проставленный revoked_at, иначе теряется доказуемость факта согласия.
CREATE TABLE core.user_consents (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id          uuid NOT NULL REFERENCES core.users (id) ON DELETE CASCADE,
    kind             core.consent_kind NOT NULL,
    document_version varchar(20) NOT NULL
                         CONSTRAINT user_consents_doc_version_not_blank CHECK (char_length(btrim(document_version)) > 0),
    granted_at       timestamptz NOT NULL DEFAULT now(),
    revoked_at       timestamptz,

    CONSTRAINT user_consents_revoked_after_granted CHECK (revoked_at IS NULL OR revoked_at >= granted_at)
);

-- Действующее согласие каждого вида может быть только одно.
-- Партиальный UNIQUE заменяет здесь триггер-сторож.
CREATE UNIQUE INDEX user_consents_active_uq
    ON core.user_consents (user_id, kind)
    WHERE revoked_at IS NULL;

-- =============================================================================
--  20. ref — справочники предметной области
-- =============================================================================
--  Вся предметная таксономия (отрасль -> специализация -> грейд -> категория,
--  область навыков -> навык) лежит в данных, а не в коде и не в ENUM-типах.
--  Это то, что позволяет загрузить банк заданий другой области и использовать
--  ту же систему вне ИТ: достаточно вставить строки, а не менять схему.
-- =============================================================================

CREATE TYPE ref.skill_kind          AS ENUM ('language', 'framework', 'tool', 'practice', 'soft', 'domain');
CREATE TYPE ref.attribute_scope     AS ENUM ('candidate', 'vacancy', 'company', 'task');
CREATE TYPE ref.attribute_data_type AS ENUM ('string', 'text', 'integer', 'decimal', 'boolean', 'date', 'option');

-- -----------------------------------------------------------------------------
--  Отрасль / специализация / грейд / категория
-- -----------------------------------------------------------------------------
CREATE TABLE ref.industries (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code        varchar(40) NOT NULL UNIQUE
                    CONSTRAINT industries_code_format CHECK (code ~ '^[a-z][a-z0-9_]{1,39}$'),
    name        varchar(100) NOT NULL
                    CONSTRAINT industries_name_not_blank CHECK (char_length(btrim(name)) > 0),
    description varchar(500),
    is_active   boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE ref.industries IS 'Отрасль. Точка расширения за пределы ИТ: новая отрасль = новая строка + свой банк заданий';

CREATE TABLE ref.specializations (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    industry_id bigint NOT NULL REFERENCES ref.industries (id) ON DELETE RESTRICT,
    -- self-FK даёт подобласти: Backend -> Backend/Python
    parent_id   bigint REFERENCES ref.specializations (id) ON DELETE RESTRICT,
    code        varchar(40) NOT NULL
                    CONSTRAINT specializations_code_format CHECK (code ~ '^[a-z][a-z0-9_]{1,39}$'),
    name        varchar(100) NOT NULL
                    CONSTRAINT specializations_name_not_blank CHECK (char_length(btrim(name)) > 0),
    description varchar(500),
    sort_order  smallint NOT NULL DEFAULT 0,
    is_active   boolean NOT NULL DEFAULT true,

    CONSTRAINT specializations_industry_code_uq UNIQUE (industry_id, code),
    CONSTRAINT specializations_no_self_parent   CHECK (parent_id IS DISTINCT FROM id)
);

CREATE TABLE ref.grades (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    industry_id           bigint NOT NULL REFERENCES ref.industries (id) ON DELETE RESTRICT,
    code                  varchar(40) NOT NULL
                              CONSTRAINT grades_code_format CHECK (code ~ '^[a-z][a-z0-9_]{1,39}$'),
    name                  varchar(50) NOT NULL
                              CONSTRAINT grades_name_not_blank CHECK (char_length(btrim(name)) > 0),
    -- порядковый уровень: по нему сравниваются «грейд ниже / выше»
    level                 smallint NOT NULL CHECK (level BETWEEN 0 AND 100),
    min_experience_months smallint CHECK (min_experience_months BETWEEN 0 AND 840),
    -- «смена грейда не чаще раза в N дней» (п. 2 ТЗ); хранится как данные,
    -- чтобы интервал можно было поменять без релиза бэкенда
    cooldown_days         smallint NOT NULL DEFAULT 90 CHECK (cooldown_days BETWEEN 0 AND 3650),

    CONSTRAINT grades_industry_code_uq  UNIQUE (industry_id, code),
    CONSTRAINT grades_industry_level_uq UNIQUE (industry_id, level)
);

COMMENT ON COLUMN ref.grades.cooldown_days IS 'Минимальный интервал между сменами грейда; бэкенд пишет из него assess.grade_assignments.next_change_allowed_at';

-- Категория из ТЗ — связка специализации и грейда — сущность первого класса:
-- на неё ссылаются кандидат, вакансия, приглашение и результат подбора.
CREATE TABLE ref.categories (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    specialization_id bigint NOT NULL REFERENCES ref.specializations (id) ON DELETE RESTRICT,
    grade_id          bigint NOT NULL REFERENCES ref.grades (id) ON DELETE RESTRICT,
    slug              varchar(90) NOT NULL UNIQUE
                          CONSTRAINT categories_slug_format CHECK (slug ~ '^[a-z][a-z0-9_.-]{1,89}$'),
    is_active         boolean NOT NULL DEFAULT true,

    CONSTRAINT categories_spec_grade_uq UNIQUE (specialization_id, grade_id)
);

COMMENT ON TABLE ref.categories IS 'Категория = специализация x грейд. Группа однотипных соискателей, которую видит работодатель';

-- -----------------------------------------------------------------------------
--  Навыки
-- -----------------------------------------------------------------------------
CREATE TABLE ref.skill_areas (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    parent_id  bigint REFERENCES ref.skill_areas (id) ON DELETE RESTRICT,
    code       varchar(40) NOT NULL UNIQUE
                   CONSTRAINT skill_areas_code_format CHECK (code ~ '^[a-z][a-z0-9_]{1,39}$'),
    name       varchar(80) NOT NULL
                   CONSTRAINT skill_areas_name_not_blank CHECK (char_length(btrim(name)) > 0),
    sort_order smallint NOT NULL DEFAULT 0,

    CONSTRAINT skill_areas_no_self_parent CHECK (parent_id IS DISTINCT FROM id)
);

COMMENT ON TABLE ref.skill_areas IS 'Области навыков (ML, Web, DevOps, SoftSkills) и подобласти через parent_id';

CREATE TABLE ref.skills (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    area_id   bigint NOT NULL REFERENCES ref.skill_areas (id) ON DELETE RESTRICT,
    kind      ref.skill_kind NOT NULL,
    code      varchar(50) NOT NULL UNIQUE
                  CONSTRAINT skills_code_format CHECK (code ~ '^[a-z][a-z0-9_+.-]{1,49}$'),
    name      varchar(60) NOT NULL
                  CONSTRAINT skills_name_not_blank CHECK (char_length(btrim(name)) > 0),
    is_active boolean NOT NULL DEFAULT true
);

COMMENT ON COLUMN ref.skills.kind IS 'ЯП / фреймворк / инструмент / практика / soft-skill — в UI это разные поля одной области';

-- Синонимы для разбора резюме и ответов LLM: «Постгрес», «psql», «PostgreSQL» -> один skill_id.
-- citext снимает необходимость в lower()-индексе и в нормализации на бэке.
CREATE TABLE ref.skill_aliases (
    id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    skill_id bigint NOT NULL REFERENCES ref.skills (id) ON DELETE CASCADE,
    alias    citext NOT NULL UNIQUE
                 CONSTRAINT skill_aliases_len CHECK (char_length(alias) BETWEEN 1 AND 60)
);

-- Вес навыка в специализации — вход для префильтра подборки
CREATE TABLE ref.specialization_skills (
    specialization_id bigint NOT NULL REFERENCES ref.specializations (id) ON DELETE CASCADE,
    skill_id          bigint NOT NULL REFERENCES ref.skills (id) ON DELETE CASCADE,
    weight            numeric(3, 2) NOT NULL DEFAULT 1.00 CHECK (weight > 0 AND weight <= 9.99),
    is_core           boolean NOT NULL DEFAULT false,

    PRIMARY KEY (specialization_id, skill_id)
);

-- Роль в команде (ТЗ: «стек, грейд, роль в команде, формат работы»)
CREATE TABLE ref.team_roles (
    id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code varchar(40) NOT NULL UNIQUE
             CONSTRAINT team_roles_code_format CHECK (code ~ '^[a-z][a-z0-9_]{1,39}$'),
    name varchar(60) NOT NULL
             CONSTRAINT team_roles_name_not_blank CHECK (char_length(btrim(name)) > 0)
);

-- -----------------------------------------------------------------------------
--  EAV: описание атрибутов, которых нет в фиксированных колонках
-- -----------------------------------------------------------------------------
--  Фиксированные колонки покрывают то, что одинаково для любой отрасли (ФИО,
--  стаж, формат работы). Всё, что специфично для отрасли, описывается здесь и
--  хранится в *_attribute_values. Так «донастройка под другую нишу» не требует
--  ALTER TABLE и не превращает профиль в свалку jsonb без схемы.
-- -----------------------------------------------------------------------------
CREATE TABLE ref.attribute_defs (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scope       ref.attribute_scope NOT NULL,
    -- NULL = атрибут общий для всех отраслей
    industry_id bigint REFERENCES ref.industries (id) ON DELETE CASCADE,
    code        varchar(50) NOT NULL
                    CONSTRAINT attribute_defs_code_format CHECK (code ~ '^[a-z][a-z0-9_]{1,49}$'),
    name        varchar(100) NOT NULL
                    CONSTRAINT attribute_defs_name_not_blank CHECK (char_length(btrim(name)) > 0),
    data_type   ref.attribute_data_type NOT NULL,
    unit        varchar(20),
    is_multi    boolean NOT NULL DEFAULT false,
    is_required boolean NOT NULL DEFAULT false,
    -- видно ли работодателю по умолчанию
    is_public   boolean NOT NULL DEFAULT true,
    sort_order  smallint NOT NULL DEFAULT 0,

    CONSTRAINT attribute_defs_scope_code_uq UNIQUE (scope, code),
    -- целевая таблица значений ссылается на пару (id, data_type) составным FK,
    -- благодаря чему тип значения нельзя разойтись с объявлением атрибута
    CONSTRAINT attribute_defs_id_type_uq    UNIQUE (id, data_type),
    CONSTRAINT attribute_defs_multi_only_for_option
        CHECK (NOT is_multi OR data_type = 'option')
);

CREATE TABLE ref.attribute_options (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    attribute_id bigint NOT NULL REFERENCES ref.attribute_defs (id) ON DELETE CASCADE,
    code         varchar(50) NOT NULL
                     CONSTRAINT attribute_options_code_format CHECK (code ~ '^[a-z][a-z0-9_]{1,49}$'),
    label        varchar(100) NOT NULL
                     CONSTRAINT attribute_options_label_not_blank CHECK (char_length(btrim(label)) > 0),
    sort_order   smallint NOT NULL DEFAULT 0,

    CONSTRAINT attribute_options_attr_code_uq UNIQUE (attribute_id, code),
    -- для составного FK из таблиц значений: вариант обязан принадлежать своему атрибуту
    CONSTRAINT attribute_options_attr_id_uq   UNIQUE (attribute_id, id)
);

-- =============================================================================
--  30. profile — профиль кандидата
-- =============================================================================
--  Анкета, резюме (загруженное PDF и сгенерированное), подтверждённые
--  достижения ФСП и сигналы из GitHub. Приватность и предпочтения вынесены в
--  отдельные таблицы 1:1 — они читаются разными сценариями и меняются
--  независимо от самой анкеты.
-- =============================================================================

CREATE TYPE profile.work_format        AS ENUM ('office', 'hybrid', 'remote');
CREATE TYPE profile.employment_type    AS ENUM ('full_time', 'part_time', 'project', 'internship', 'contract');
CREATE TYPE profile.link_kind          AS ENUM ('github', 'gitlab', 'telegram', 'portfolio', 'linkedin', 'habr', 'other');
CREATE TYPE profile.skill_source       AS ENUM ('self', 'resume', 'test', 'github', 'fsp');
CREATE TYPE profile.resume_kind        AS ENUM ('uploaded', 'generated');
CREATE TYPE profile.parse_status       AS ENUM ('pending', 'processing', 'done', 'failed');
CREATE TYPE profile.fsp_link_status    AS ENUM ('unlinked', 'pending', 'verified', 'failed');
CREATE TYPE profile.fsp_event_level    AS ENUM ('local', 'regional', 'federal', 'international');
CREATE TYPE profile.achievement_source AS ENUM ('registry', 'manual');

-- -----------------------------------------------------------------------------
--  Анкета
-- -----------------------------------------------------------------------------
CREATE TABLE profile.candidates (
    -- PK = FK: кандидат это проекция пользователя, отдельный суррогат не нужен
    user_id              uuid PRIMARY KEY REFERENCES core.users (id) ON DELETE CASCADE,
    first_name           varchar(50) NOT NULL
                             CONSTRAINT candidates_first_name_not_blank CHECK (char_length(btrim(first_name)) > 0),
    last_name            varchar(50) NOT NULL
                             CONSTRAINT candidates_last_name_not_blank CHECK (char_length(btrim(last_name)) > 0),
    middle_name          varchar(50),
    birth_date           date CONSTRAINT candidates_birth_date_sane CHECK (birth_date BETWEEN DATE '1930-01-01' AND DATE '2020-01-01'),
    phone                varchar(20)
                             CONSTRAINT candidates_phone_format CHECK (phone ~ '^\+?[0-9]{7,15}$'),
    city                 varchar(100),
    relocation           boolean NOT NULL DEFAULT false,
    work_format          profile.work_format,
    employment_type      profile.employment_type,
    -- 840 месяцев = 70 лет, верхняя граница здравого смысла
    experience_months    smallint NOT NULL DEFAULT 0 CHECK (experience_months BETWEEN 0 AND 840),
    about                text CONSTRAINT candidates_about_len CHECK (char_length(about) <= 4000),

    -- текущая категория; присваивается по результату теста, не со слов кандидата
    current_category_id  bigint REFERENCES ref.categories (id) ON DELETE SET NULL,
    is_open_to_offers    boolean NOT NULL DEFAULT true,

    -- «сила подтверждённого профиля» — ранжирование внутри категории (п. 2.1 ТЗ).
    -- Считает бэкенд из результатов тестов, достижений ФСП и свежести профиля.
    profile_strength     numeric(5, 2) NOT NULL DEFAULT 0 CHECK (profile_strength BETWEEN 0 AND 100),
    profile_refreshed_at timestamptz,

    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),

    -- Генерируемая колонка, а не триггер: полнотекстовый вектор пересчитывается самим PostgreSQL
    search_vector        tsvector GENERATED ALWAYS AS (
                             to_tsvector('russian',
                                 coalesce(first_name, '') || ' ' ||
                                 coalesce(last_name, '')  || ' ' ||
                                 coalesce(city, '')       || ' ' ||
                                 coalesce(about, ''))
                         ) STORED
);

COMMENT ON COLUMN profile.candidates.current_category_id IS 'Денормализованный указатель на текущую категорию; источник истины — assess.grade_assignments с is_current';

-- Соцсети и отдельная ссылка на сайт-портфолио
CREATE TABLE profile.candidate_links (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    candidate_id uuid NOT NULL REFERENCES profile.candidates (user_id) ON DELETE CASCADE,
    kind         profile.link_kind NOT NULL,
    url          varchar(2048) NOT NULL
                     CONSTRAINT candidate_links_url_format CHECK (url ~ '^https?://[^[:space:]]{3,}$'),
    label        varchar(60),
    is_public    boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT candidate_links_unique UNIQUE (candidate_id, kind, url)
);

CREATE TABLE profile.candidate_skills (
    candidate_id    uuid NOT NULL REFERENCES profile.candidates (user_id) ON DELETE CASCADE,
    skill_id        bigint NOT NULL REFERENCES ref.skills (id) ON DELETE RESTRICT,
    -- что заявил сам кандидат
    self_level      smallint NOT NULL DEFAULT 0 CHECK (self_level BETWEEN 0 AND 5),
    -- что подтвердилось тестом; NULL = не проверялось. Именно это видит работодатель как «подтверждённое»
    confirmed_level smallint CHECK (confirmed_level BETWEEN 0 AND 5),
    years           numeric(3, 1) CHECK (years >= 0 AND years <= 70),
    source          profile.skill_source NOT NULL DEFAULT 'self',
    updated_at      timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (candidate_id, skill_id)
);

CREATE TABLE profile.candidate_team_roles (
    candidate_id uuid NOT NULL REFERENCES profile.candidates (user_id) ON DELETE CASCADE,
    team_role_id bigint NOT NULL REFERENCES ref.team_roles (id) ON DELETE RESTRICT,

    PRIMARY KEY (candidate_id, team_role_id)
);

-- Предпочтения кандидата, включая свободные позитивный/негативный промты:
-- они идут в эмбеддинг и в контекст LLM при подборе
CREATE TABLE profile.candidate_preferences (
    candidate_id             uuid PRIMARY KEY REFERENCES profile.candidates (user_id) ON DELETE CASCADE,
    desired_specialization_id bigint REFERENCES ref.specializations (id) ON DELETE SET NULL,
    salary_min               integer CHECK (salary_min > 0 AND salary_min <= 100000000),
    salary_max               integer CHECK (salary_max > 0 AND salary_max <= 100000000),
    currency                 char(3) NOT NULL DEFAULT 'RUB' CHECK (currency ~ '^[A-Z]{3}$'),
    positive_prompt          varchar(1000),
    negative_prompt          varchar(1000),
    updated_at               timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT candidate_preferences_salary_range CHECK (salary_max IS NULL OR salary_min IS NULL OR salary_max >= salary_min)
);

-- Что кандидат разрешает показывать работодателю (п. 2.1 ТЗ, настройки приватности)
CREATE TABLE profile.candidate_privacy (
    candidate_id  uuid PRIMARY KEY REFERENCES profile.candidates (user_id) ON DELETE CASCADE,
    is_searchable boolean NOT NULL DEFAULT true,
    show_contacts boolean NOT NULL DEFAULT false,
    show_salary   boolean NOT NULL DEFAULT true,
    show_fsp      boolean NOT NULL DEFAULT true,
    show_github   boolean NOT NULL DEFAULT true,
    updated_at    timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN profile.candidate_privacy.show_contacts IS 'Даже при true контакты отдаются только при наличии записи в hire.contact_disclosures';

-- -----------------------------------------------------------------------------
--  EAV-значения профиля
-- -----------------------------------------------------------------------------
--  Два ограничения вместе дают полную типовую гарантию без единого триггера:
--   1) составной FK (attribute_id, data_type) -> ref.attribute_defs(id, data_type)
--      не даёт подменить тип атрибута;
--   2) CHECK по data_type требует заполнить ровно ту колонку, которая этому типу
--      соответствует, а второй CHECK запрещает заполнять больше одной.
-- -----------------------------------------------------------------------------
CREATE TABLE profile.candidate_attribute_values (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    candidate_id  uuid NOT NULL REFERENCES profile.candidates (user_id) ON DELETE CASCADE,
    attribute_id  bigint NOT NULL,
    data_type     ref.attribute_data_type NOT NULL,

    value_string  varchar(255),
    value_text    text CONSTRAINT cav_text_len CHECK (char_length(value_text) <= 4000),
    value_integer integer,
    value_decimal numeric(14, 4),
    value_boolean boolean,
    value_date    date,
    option_id     bigint,

    updated_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT cav_attribute_fk FOREIGN KEY (attribute_id, data_type)
        REFERENCES ref.attribute_defs (id, data_type) ON DELETE CASCADE,
    CONSTRAINT cav_option_fk FOREIGN KEY (attribute_id, option_id)
        REFERENCES ref.attribute_options (attribute_id, id) ON DELETE RESTRICT,

    CONSTRAINT cav_value_matches_type CHECK (
           (data_type = 'string'  AND value_string  IS NOT NULL)
        OR (data_type = 'text'    AND value_text    IS NOT NULL)
        OR (data_type = 'integer' AND value_integer IS NOT NULL)
        OR (data_type = 'decimal' AND value_decimal IS NOT NULL)
        OR (data_type = 'boolean' AND value_boolean IS NOT NULL)
        OR (data_type = 'date'    AND value_date    IS NOT NULL)
        OR (data_type = 'option'  AND option_id     IS NOT NULL)
    ),
    CONSTRAINT cav_exactly_one_value CHECK (
        (value_string  IS NOT NULL)::int +
        (value_text    IS NOT NULL)::int +
        (value_integer IS NOT NULL)::int +
        (value_decimal IS NOT NULL)::int +
        (value_boolean IS NOT NULL)::int +
        (value_date    IS NOT NULL)::int +
        (option_id     IS NOT NULL)::int = 1
    ),
    -- NULLS NOT DISTINCT: для не-option атрибута (option_id IS NULL)
    -- второе значение того же атрибута у того же кандидата невозможно
    CONSTRAINT cav_unique_value UNIQUE NULLS NOT DISTINCT (candidate_id, attribute_id, option_id)
);

-- -----------------------------------------------------------------------------
--  Резюме
-- -----------------------------------------------------------------------------
CREATE TABLE profile.resume_documents (
    id           uuid PRIMARY KEY DEFAULT uuidv7(),
    candidate_id uuid NOT NULL REFERENCES profile.candidates (user_id) ON DELETE CASCADE,
    kind         profile.resume_kind NOT NULL,
    storage_key  varchar(512) NOT NULL
                     CONSTRAINT resume_documents_key_not_blank CHECK (char_length(btrim(storage_key)) > 0),
    file_name    varchar(255),
    mime_type    varchar(100) NOT NULL DEFAULT 'application/pdf',
    size_bytes   integer CHECK (size_bytes > 0 AND size_bytes <= 20971520),
    parse_status profile.parse_status NOT NULL DEFAULT 'pending',
    -- извлечённый текст: вход для парсинга и для эмбеддинга
    raw_text     text CONSTRAINT resume_documents_raw_text_len CHECK (char_length(raw_text) <= 200000),
    uploaded_at  timestamptz NOT NULL DEFAULT now(),
    parsed_at    timestamptz,

    CONSTRAINT resume_documents_generated_is_done
        CHECK (kind <> 'generated' OR parse_status = 'done')
);

COMMENT ON TABLE profile.resume_documents IS 'И загруженное кандидатом PDF (kind=uploaded, распознаётся), и сгенерированный платформой профиль (kind=generated)';

-- Результат разбора храним отдельно от исходника: разбор можно переиграть
-- другой моделью, не теряя ни файл, ни предыдущую версию извлечённых полей
CREATE TABLE profile.resume_parse_results (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id   uuid NOT NULL REFERENCES profile.resume_documents (id) ON DELETE CASCADE,
    model         varchar(60) NOT NULL,
    payload       jsonb NOT NULL,
    confidence    numeric(3, 2) CHECK (confidence BETWEEN 0 AND 1),
    applied_at    timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN profile.resume_parse_results.applied_at IS 'Когда извлечённые поля были перенесены в анкету; NULL — разобрано, но кандидат ещё не подтвердил';

-- -----------------------------------------------------------------------------
--  ФСП
-- -----------------------------------------------------------------------------
--  Отсутствие истории ФСП — штатное состояние, а не исключение: строка
--  существует всегда, со статусом unlinked. Выдача и ранжирование обязаны
--  корректно работать для таких кандидатов (явное требование ТЗ).
-- -----------------------------------------------------------------------------
CREATE TABLE profile.fsp_profiles (
    candidate_id        uuid PRIMARY KEY REFERENCES profile.candidates (user_id) ON DELETE CASCADE,
    fsp_participant_id  varchar(64) UNIQUE,
    verification_status profile.fsp_link_status NOT NULL DEFAULT 'unlinked',
    raw_payload         jsonb,
    linked_at           timestamptz,
    synced_at           timestamptz,

    CONSTRAINT fsp_profiles_verified_has_id
        CHECK (verification_status <> 'verified' OR fsp_participant_id IS NOT NULL)
);

CREATE TABLE profile.fsp_events (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    external_id varchar(64) UNIQUE,
    title       varchar(200) NOT NULL
                    CONSTRAINT fsp_events_title_not_blank CHECK (char_length(btrim(title)) > 0),
    discipline  varchar(100),
    level       profile.fsp_event_level NOT NULL,
    starts_on   date,
    ends_on     date,

    CONSTRAINT fsp_events_period CHECK (ends_on IS NULL OR starts_on IS NULL OR ends_on >= starts_on)
);

CREATE TABLE profile.fsp_achievements (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    candidate_id   uuid NOT NULL REFERENCES profile.candidates (user_id) ON DELETE CASCADE,
    event_id       bigint NOT NULL REFERENCES profile.fsp_events (id) ON DELETE RESTRICT,
    place          smallint CHECK (place > 0 AND place <= 10000),
    participants   integer CHECK (participants > 0),
    team_name      varchar(100),
    score          numeric(10, 3),
    source         profile.achievement_source NOT NULL DEFAULT 'registry',
    confirmed_at   timestamptz,
    raw            jsonb,

    CONSTRAINT fsp_achievements_unique UNIQUE (candidate_id, event_id),
    CONSTRAINT fsp_achievements_place_within_participants
        CHECK (participants IS NULL OR place IS NULL OR place <= participants)
);

-- -----------------------------------------------------------------------------
--  GitHub
-- -----------------------------------------------------------------------------
CREATE TABLE profile.github_accounts (
    candidate_id      uuid PRIMARY KEY REFERENCES profile.candidates (user_id) ON DELETE CASCADE,
    github_user_id    bigint NOT NULL UNIQUE,
    -- предел длины логина на стороне GitHub — 39 символов
    login             varchar(39) NOT NULL UNIQUE
                          CONSTRAINT github_accounts_login_format CHECK (login ~ '^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$'),
    installation_id   bigint,
    -- токен хранится зашифрованным приложением; БД видит только шифротекст
    access_token_enc  bytea,
    scopes            varchar(255),
    linked_at         timestamptz NOT NULL DEFAULT now(),
    indexed_at        timestamptz
);

CREATE TABLE profile.github_repos (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    candidate_id     uuid NOT NULL REFERENCES profile.github_accounts (candidate_id) ON DELETE CASCADE,
    github_repo_id   bigint NOT NULL UNIQUE,
    full_name        varchar(140) NOT NULL,
    description      varchar(500),
    is_private       boolean NOT NULL DEFAULT false,
    is_fork          boolean NOT NULL DEFAULT false,
    stars            integer NOT NULL DEFAULT 0 CHECK (stars >= 0),
    forks            integer NOT NULL DEFAULT 0 CHECK (forks >= 0),
    commits_by_owner integer CHECK (commits_by_owner >= 0),
    primary_language varchar(40),
    pushed_at        timestamptz,
    indexed_at       timestamptz
);

CREATE TABLE profile.github_repo_languages (
    repo_id  bigint NOT NULL REFERENCES profile.github_repos (id) ON DELETE CASCADE,
    language varchar(40) NOT NULL,
    bytes    bigint NOT NULL CHECK (bytes > 0),

    PRIMARY KEY (repo_id, language)
);

-- Вывод индексации: «по репозиториям видно владение навыком X на уровне Y».
-- Именно отсюда бэкенд обновляет candidate_skills с source='github'.
CREATE TABLE profile.github_signals (
    candidate_id uuid NOT NULL REFERENCES profile.candidates (user_id) ON DELETE CASCADE,
    skill_id     bigint NOT NULL REFERENCES ref.skills (id) ON DELETE CASCADE,
    score        numeric(5, 2) NOT NULL CHECK (score BETWEEN 0 AND 100),
    evidence     jsonb,
    computed_at  timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (candidate_id, skill_id)
);

-- =============================================================================
--  40. assess — опрос, задания, тестирование, грейд
-- =============================================================================
--  Главная проблема механики из ТЗ: единый набор заданий утечёт, а чисто
--  случайная генерация не даёт сопоставимой сложности. Схема разводит два
--  понятия:
--    task_templates — калиброванный шаблон (специализация, грейд, навык,
--                     сложность, рубрика оценивания). Сравнимость берётся отсюда.
--    tasks          — уникальный экземпляр, сгенерированный под конкретного
--                     кандидата из шаблона. Утечка экземпляра не обесценивает
--                     шаблон, а экземпляр не переиспользуется.
--  Банк шаблонов привязан к специализации и грейду, то есть к данным ref, —
--  поэтому его можно заменить банком любой другой области.
-- =============================================================================

CREATE TYPE assess.question_format  AS ENUM ('single_choice', 'multi_choice', 'open', 'code', 'case', 'scale');
CREATE TYPE assess.template_source  AS ENUM ('manual', 'llm', 'imported');
CREATE TYPE assess.session_status   AS ENUM ('created', 'in_progress', 'submitted', 'scored', 'expired', 'cancelled');
CREATE TYPE assess.session_outcome  AS ENUM ('passed', 'failed', 'downgraded', 'upgraded');
CREATE TYPE assess.checked_by       AS ENUM ('auto', 'llm', 'human');
CREATE TYPE assess.grade_source     AS ENUM ('test', 'manual', 'admin', 'import');
CREATE TYPE assess.assignment_status AS ENUM ('issued', 'viewed', 'answered', 'scored', 'expired', 'skipped');

-- -----------------------------------------------------------------------------
--  Опрос по отрасли и специализации
-- -----------------------------------------------------------------------------
CREATE TABLE assess.survey_templates (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    industry_id bigint NOT NULL REFERENCES ref.industries (id) ON DELETE CASCADE,
    code        varchar(40) NOT NULL UNIQUE,
    title       varchar(120) NOT NULL,
    version     smallint NOT NULL DEFAULT 1 CHECK (version > 0),
    is_active   boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE assess.survey_questions (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    survey_id   bigint NOT NULL REFERENCES assess.survey_templates (id) ON DELETE CASCADE,
    order_no    smallint NOT NULL CHECK (order_no > 0),
    format      assess.question_format NOT NULL,
    text        varchar(500) NOT NULL
                    CONSTRAINT survey_questions_text_not_blank CHECK (char_length(btrim(text)) > 0),
    is_required boolean NOT NULL DEFAULT true,

    CONSTRAINT survey_questions_order_uq UNIQUE (survey_id, order_no)
);

-- Вариант ответа может «вести» на специализацию или навык — так опрос
-- превращается в предварительную классификацию кандидата ещё до теста
CREATE TABLE assess.survey_options (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    question_id       bigint NOT NULL REFERENCES assess.survey_questions (id) ON DELETE CASCADE,
    order_no          smallint NOT NULL CHECK (order_no > 0),
    label             varchar(200) NOT NULL,
    specialization_id bigint REFERENCES ref.specializations (id) ON DELETE SET NULL,
    skill_id          bigint REFERENCES ref.skills (id) ON DELETE SET NULL,
    weight            numeric(4, 2) NOT NULL DEFAULT 1.00,

    CONSTRAINT survey_options_order_uq UNIQUE (question_id, order_no)
);

CREATE TABLE assess.survey_sessions (
    id             uuid PRIMARY KEY DEFAULT uuidv7(),
    candidate_id   uuid NOT NULL REFERENCES profile.candidates (user_id) ON DELETE CASCADE,
    survey_id      bigint NOT NULL REFERENCES assess.survey_templates (id) ON DELETE RESTRICT,
    status         assess.session_status NOT NULL DEFAULT 'created',
    -- что получилось по итогам опроса: предложенная специализация и заявленный грейд
    specialization_id bigint REFERENCES ref.specializations (id) ON DELETE SET NULL,
    claimed_grade_id  bigint REFERENCES ref.grades (id) ON DELETE SET NULL,
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,

    CONSTRAINT survey_sessions_finish_after_start CHECK (finished_at IS NULL OR finished_at >= started_at)
);

CREATE TABLE assess.survey_answers (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id  uuid NOT NULL REFERENCES assess.survey_sessions (id) ON DELETE CASCADE,
    question_id bigint NOT NULL REFERENCES assess.survey_questions (id) ON DELETE CASCADE,
    option_id   bigint REFERENCES assess.survey_options (id) ON DELETE CASCADE,
    free_text   varchar(1000),
    answered_at timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT survey_answers_has_value CHECK (option_id IS NOT NULL OR free_text IS NOT NULL),
    CONSTRAINT survey_answers_unique UNIQUE NULLS NOT DISTINCT (session_id, question_id, option_id)
);

-- -----------------------------------------------------------------------------
--  Банк заданий
-- -----------------------------------------------------------------------------
CREATE TABLE assess.task_templates (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    specialization_id bigint NOT NULL REFERENCES ref.specializations (id) ON DELETE CASCADE,
    grade_id          bigint NOT NULL REFERENCES ref.grades (id) ON DELETE CASCADE,
    skill_id          bigint REFERENCES ref.skills (id) ON DELETE SET NULL,
    code              varchar(50) NOT NULL UNIQUE,
    title             varchar(200) NOT NULL,
    format            assess.question_format NOT NULL,
    difficulty        smallint NOT NULL CHECK (difficulty BETWEEN 1 AND 10),
    -- ожидаемое время решения: по нему собирается тест заданной длительности
    expected_minutes  smallint CHECK (expected_minutes BETWEEN 1 AND 240),
    max_score         numeric(6, 2) NOT NULL DEFAULT 1 CHECK (max_score > 0),
    -- промт генератора экземпляров и рубрика проверки — то, что делает задания
    -- разными по формулировке, но одинаковыми по сложности
    generator_prompt  text CONSTRAINT task_templates_gen_prompt_len CHECK (char_length(generator_prompt) <= 8000),
    grading_rubric    text CONSTRAINT task_templates_rubric_len CHECK (char_length(grading_rubric) <= 8000),
    params_schema     jsonb,
    source            assess.template_source NOT NULL DEFAULT 'manual',
    version           smallint NOT NULL DEFAULT 1 CHECK (version > 0),
    is_active         boolean NOT NULL DEFAULT true,
    -- накопленная статистика калибровки: дискриминативность задания
    usage_count       integer NOT NULL DEFAULT 0 CHECK (usage_count >= 0),
    avg_score         numeric(5, 2) CHECK (avg_score >= 0),
    created_at        timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE assess.task_templates IS 'Калиброванный шаблон задания. Банк под другую отрасль загружается сюда же — схема от ИТ не зависит';

-- Экземпляр задания под конкретного кандидата. Одинаковых экземпляров у разных
-- кандидатов не бывает — это и есть защита от расхождения заданий по рынку.
CREATE TABLE assess.tasks (
    id            uuid PRIMARY KEY DEFAULT uuidv7(),
    template_id   bigint NOT NULL REFERENCES assess.task_templates (id) ON DELETE RESTRICT,
    candidate_id  uuid REFERENCES profile.candidates (user_id) ON DELETE CASCADE,
    statement     text NOT NULL
                      CONSTRAINT tasks_statement_len CHECK (char_length(statement) BETWEEN 1 AND 20000),
    payload       jsonb,
    answer_key    jsonb,
    difficulty    smallint NOT NULL CHECK (difficulty BETWEEN 1 AND 10),
    max_score     numeric(6, 2) NOT NULL CHECK (max_score > 0),
    generated_by  varchar(60),
    generation_seed bigint,
    created_at    timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz
);

COMMENT ON COLUMN assess.tasks.answer_key IS 'Эталон/тесты. Отдавать клиенту запрещено — выборка полей делается на бэкенде';

-- -----------------------------------------------------------------------------
--  Тестирование
-- -----------------------------------------------------------------------------
CREATE TABLE assess.test_sessions (
    id                uuid PRIMARY KEY DEFAULT uuidv7(),
    candidate_id      uuid NOT NULL REFERENCES profile.candidates (user_id) ON DELETE CASCADE,
    survey_session_id uuid REFERENCES assess.survey_sessions (id) ON DELETE SET NULL,
    specialization_id bigint NOT NULL REFERENCES ref.specializations (id) ON DELETE RESTRICT,
    -- грейд, который кандидат заявил сам: тест проверяет именно его
    claimed_grade_id  bigint NOT NULL REFERENCES ref.grades (id) ON DELETE RESTRICT,
    status            assess.session_status NOT NULL DEFAULT 'created',
    outcome           assess.session_outcome,
    -- категория, присвоенная по результату; может отличаться от заявленной
    assigned_category_id bigint REFERENCES ref.categories (id) ON DELETE SET NULL,
    total_score       numeric(7, 2) CHECK (total_score >= 0),
    max_score         numeric(7, 2) CHECK (max_score > 0),
    started_at        timestamptz,
    deadline_at       timestamptz,
    finished_at       timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT test_sessions_score_within_max CHECK (total_score IS NULL OR max_score IS NULL OR total_score <= max_score),
    CONSTRAINT test_sessions_finish_after_start CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at),
    CONSTRAINT test_sessions_scored_has_result CHECK (status <> 'scored' OR (outcome IS NOT NULL AND total_score IS NOT NULL))
);

CREATE TABLE assess.test_session_tasks (
    session_id uuid NOT NULL REFERENCES assess.test_sessions (id) ON DELETE CASCADE,
    task_id    uuid NOT NULL REFERENCES assess.tasks (id) ON DELETE RESTRICT,
    order_no   smallint NOT NULL CHECK (order_no > 0),
    weight     numeric(4, 2) NOT NULL DEFAULT 1.00 CHECK (weight > 0),

    PRIMARY KEY (session_id, task_id),
    CONSTRAINT test_session_tasks_order_uq UNIQUE (session_id, order_no),
    -- один и тот же экземпляр задания не может попасть в две сессии
    CONSTRAINT test_session_tasks_task_uq   UNIQUE (task_id)
);

CREATE TABLE assess.task_answers (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id       uuid NOT NULL REFERENCES assess.test_sessions (id) ON DELETE CASCADE,
    task_id          uuid NOT NULL REFERENCES assess.tasks (id) ON DELETE CASCADE,
    answer_text      text CONSTRAINT task_answers_text_len CHECK (char_length(answer_text) <= 50000),
    answer_payload   jsonb,
    score            numeric(6, 2) CHECK (score >= 0),
    max_score        numeric(6, 2) CHECK (max_score > 0),
    checked_by       assess.checked_by,
    feedback         varchar(2000),
    submitted_at     timestamptz NOT NULL DEFAULT now(),
    checked_at       timestamptz,

    -- сигналы честности прохождения: нужны для оценки устойчивости механики
    time_spent_sec   integer CHECK (time_spent_sec >= 0),
    paste_count      smallint CHECK (paste_count >= 0),
    focus_lost_count smallint CHECK (focus_lost_count >= 0),

    CONSTRAINT task_answers_unique UNIQUE (session_id, task_id),
    CONSTRAINT task_answers_score_within_max CHECK (score IS NULL OR max_score IS NULL OR score <= max_score)
);

-- -----------------------------------------------------------------------------
--  История грейдов
-- -----------------------------------------------------------------------------
--  Грейд не понижается принудительно: не сдавший тест на заявленный уровень
--  может пройти тест на уровень ниже, сдавший уверенно — на уровень выше.
--  Поэтому таблица append-only, а «текущий» помечен флагом.
--  next_change_allowed_at закрывает требование «не чаще одного раза в N месяцев»:
--  бэкенд сравнивает с now() перед выдачей новой тестовой сессии.
-- -----------------------------------------------------------------------------
CREATE TABLE assess.grade_assignments (
    id                     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    candidate_id           uuid NOT NULL REFERENCES profile.candidates (user_id) ON DELETE CASCADE,
    category_id            bigint NOT NULL REFERENCES ref.categories (id) ON DELETE RESTRICT,
    source                 assess.grade_source NOT NULL DEFAULT 'test',
    test_session_id        uuid REFERENCES assess.test_sessions (id) ON DELETE SET NULL,
    assigned_at            timestamptz NOT NULL DEFAULT now(),
    next_change_allowed_at timestamptz NOT NULL,
    is_current             boolean NOT NULL DEFAULT true,
    note                   varchar(300),

    CONSTRAINT grade_assignments_cooldown_in_future CHECK (next_change_allowed_at >= assigned_at),
    CONSTRAINT grade_assignments_test_source_has_session
        CHECK (source <> 'test' OR test_session_id IS NOT NULL)
);

-- Ровно одна действующая категория на кандидата — партиальным UNIQUE,
-- а не триггером-сторожем
CREATE UNIQUE INDEX grade_assignments_current_uq
    ON assess.grade_assignments (candidate_id)
    WHERE is_current;

-- -----------------------------------------------------------------------------
--  Регулярные короткие задания от работодателя
-- -----------------------------------------------------------------------------
CREATE TABLE assess.regular_assignments (
    id           uuid PRIMARY KEY DEFAULT uuidv7(),
    candidate_id uuid NOT NULL REFERENCES profile.candidates (user_id) ON DELETE CASCADE,
    task_id      uuid NOT NULL REFERENCES assess.tasks (id) ON DELETE RESTRICT,
    -- FK на hire.companies добавляется в 50_hire.sql: модуль hire создаётся позже
    company_id   uuid,
    status       assess.assignment_status NOT NULL DEFAULT 'issued',
    answer_text  text CONSTRAINT regular_assignments_answer_len CHECK (char_length(answer_text) <= 20000),
    score        numeric(6, 2) CHECK (score >= 0),
    max_score    numeric(6, 2) CHECK (max_score > 0),
    issued_at    timestamptz NOT NULL DEFAULT now(),
    due_at       timestamptz,
    answered_at  timestamptz,

    CONSTRAINT regular_assignments_due_after_issue CHECK (due_at IS NULL OR due_at > issued_at),
    CONSTRAINT regular_assignments_score_within_max CHECK (score IS NULL OR max_score IS NULL OR score <= max_score)
);

COMMENT ON TABLE assess.regular_assignments IS 'Короткая задача «от работодателя»: поддерживает профиль в актуальном состоянии (profile.candidates.profile_refreshed_at)';

-- =============================================================================
--  50. hire — работодатель, вакансии, приглашения, отклики
-- =============================================================================
--  Смысловой центр продукта — приглашение (invitations): инициативу проявляет
--  работодатель, а не кандидат. Поэтому приглашение самодостаточно: оно может
--  существовать без вакансии, обязано нести зарплатную вилку и способ связи.
--
--  Правила видимости из п. 2.2 ТЗ закодированы ограничениями, а не только
--  логикой хендлеров:
--    * вилка NOT NULL и в вакансии, и в приглашении;
--    * контакты кандидата раскрываются работодателю только через запись в
--      contact_disclosures, которая появляется при принятии приглашения или
--      при самостоятельном отклике;
--    * статусы ведутся в append-only журнале, обе стороны видят одно и то же.
-- =============================================================================

CREATE TYPE hire.company_size        AS ENUM ('micro', 'small', 'medium', 'large', 'enterprise');
CREATE TYPE hire.vacancy_status      AS ENUM ('draft', 'published', 'paused', 'archived');
CREATE TYPE hire.skill_importance    AS ENUM ('required', 'nice_to_have');
CREATE TYPE hire.invitation_status   AS ENUM ('sent', 'viewed', 'accepted', 'declined', 'withdrawn', 'expired');
CREATE TYPE hire.application_status  AS ENUM ('sent', 'viewed', 'invited', 'rejected', 'withdrawn');
CREATE TYPE hire.contact_method      AS ENUM ('email', 'phone', 'telegram', 'platform');
CREATE TYPE hire.disclosure_reason   AS ENUM ('invitation_accepted', 'application_sent');

-- -----------------------------------------------------------------------------
--  Компания и её сотрудники
-- -----------------------------------------------------------------------------
CREATE TABLE hire.companies (
    id          uuid PRIMARY KEY DEFAULT uuidv7(),
    name        varchar(120) NOT NULL
                    CONSTRAINT companies_name_not_blank CHECK (char_length(btrim(name)) > 0),
    legal_name  varchar(200),
    inn         varchar(12) UNIQUE
                    CONSTRAINT companies_inn_format CHECK (inn ~ '^[0-9]{10}$' OR inn ~ '^[0-9]{12}$'),
    industry_id bigint REFERENCES ref.industries (id) ON DELETE SET NULL,
    description text CONSTRAINT companies_description_len CHECK (char_length(description) <= 5000),
    website     varchar(2048)
                    CONSTRAINT companies_website_format CHECK (website ~ '^https?://[^[:space:]]{3,}$'),
    logo_url    varchar(2048),
    size        hire.company_size,
    city        varchar(100),
    -- задел под механизм защиты от недобросовестных работодателей (вынесен за MVP)
    is_verified boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE hire.employers (
    user_id       uuid PRIMARY KEY REFERENCES core.users (id) ON DELETE CASCADE,
    company_id    uuid NOT NULL REFERENCES hire.companies (id) ON DELETE CASCADE,
    full_name     varchar(120) NOT NULL
                      CONSTRAINT employers_full_name_not_blank CHECK (char_length(btrim(full_name)) > 0),
    position      varchar(100),
    contact_email citext
                      CONSTRAINT employers_contact_email_format CHECK (contact_email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[A-Za-z]{2,}$'),
    contact_phone varchar(20)
                      CONSTRAINT employers_contact_phone_format CHECK (contact_phone ~ '^\+?[0-9]{7,15}$'),
    is_owner      boolean NOT NULL DEFAULT false,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- Теперь, когда hire.companies существует, замыкаем ссылку из модуля assess
ALTER TABLE assess.regular_assignments
    ADD CONSTRAINT regular_assignments_company_fk
    FOREIGN KEY (company_id) REFERENCES hire.companies (id) ON DELETE SET NULL;

-- -----------------------------------------------------------------------------
--  Вакансии
-- -----------------------------------------------------------------------------
CREATE TABLE hire.vacancies (
    id              uuid PRIMARY KEY DEFAULT uuidv7(),
    company_id      uuid NOT NULL REFERENCES hire.companies (id) ON DELETE CASCADE,
    author_user_id  uuid REFERENCES core.users (id) ON DELETE SET NULL,
    title           varchar(120) NOT NULL
                        CONSTRAINT vacancies_title_not_blank CHECK (char_length(btrim(title)) > 0),
    description     text NOT NULL
                        CONSTRAINT vacancies_description_len CHECK (char_length(description) BETWEEN 1 AND 20000),
    category_id     bigint NOT NULL REFERENCES ref.categories (id) ON DELETE RESTRICT,
    work_format     profile.work_format,
    employment_type profile.employment_type,
    city            varchar(100),

    -- п. 2.2.1 ТЗ: уровень заработной платы указывается обязательно, диапазоном
    salary_min      integer NOT NULL CHECK (salary_min > 0 AND salary_min <= 100000000),
    salary_max      integer NOT NULL CHECK (salary_max > 0 AND salary_max <= 100000000),
    currency        char(3) NOT NULL DEFAULT 'RUB' CHECK (currency ~ '^[A-Z]{3}$'),

    status          hire.vacancy_status NOT NULL DEFAULT 'draft',
    published_at    timestamptz,
    expires_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),

    search_vector   tsvector GENERATED ALWAYS AS (
                        to_tsvector('russian', coalesce(title, '') || ' ' || coalesce(description, ''))
                    ) STORED,

    CONSTRAINT vacancies_salary_range    CHECK (salary_max >= salary_min),
    CONSTRAINT vacancies_published_has_ts CHECK (status <> 'published' OR published_at IS NOT NULL)
);

CREATE TABLE hire.vacancy_skills (
    vacancy_id uuid NOT NULL REFERENCES hire.vacancies (id) ON DELETE CASCADE,
    skill_id   bigint NOT NULL REFERENCES ref.skills (id) ON DELETE RESTRICT,
    importance hire.skill_importance NOT NULL DEFAULT 'required',
    min_level  smallint CHECK (min_level BETWEEN 0 AND 5),

    PRIMARY KEY (vacancy_id, skill_id)
);

-- -----------------------------------------------------------------------------
--  Описание потребности — вход конвейера подбора
-- -----------------------------------------------------------------------------
--  Работодатель описывает, кто нужен и чем занимается команда. Отдельно от
--  вакансии: подборку можно строить, ничего не публикуя (ровно та механика,
--  которая отличает сервис от доски объявлений).
-- -----------------------------------------------------------------------------
CREATE TABLE hire.employer_needs (
    id                uuid PRIMARY KEY DEFAULT uuidv7(),
    company_id        uuid NOT NULL REFERENCES hire.companies (id) ON DELETE CASCADE,
    author_user_id    uuid REFERENCES core.users (id) ON DELETE SET NULL,
    vacancy_id        uuid REFERENCES hire.vacancies (id) ON DELETE SET NULL,
    title             varchar(120) NOT NULL,
    description       text NOT NULL
                          CONSTRAINT employer_needs_description_len CHECK (char_length(description) BETWEEN 1 AND 20000),
    specialization_id bigint REFERENCES ref.specializations (id) ON DELETE SET NULL,
    grade_id          bigint REFERENCES ref.grades (id) ON DELETE SET NULL,
    category_id       bigint REFERENCES ref.categories (id) ON DELETE SET NULL,
    -- свободные предпочтения: идут в эмбеддинг и в системный промт LLM-отбора
    positive_prompt   varchar(2000),
    negative_prompt   varchar(2000),
    salary_min        integer CHECK (salary_min > 0),
    salary_max        integer CHECK (salary_max > 0),
    currency          char(3) NOT NULL DEFAULT 'RUB' CHECK (currency ~ '^[A-Z]{3}$'),
    is_active         boolean NOT NULL DEFAULT true,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT employer_needs_salary_range CHECK (salary_max IS NULL OR salary_min IS NULL OR salary_max >= salary_min)
);

CREATE TABLE hire.need_skills (
    need_id    uuid NOT NULL REFERENCES hire.employer_needs (id) ON DELETE CASCADE,
    skill_id   bigint NOT NULL REFERENCES ref.skills (id) ON DELETE RESTRICT,
    importance hire.skill_importance NOT NULL DEFAULT 'required',
    min_level  smallint CHECK (min_level BETWEEN 0 AND 5),

    PRIMARY KEY (need_id, skill_id)
);

-- -----------------------------------------------------------------------------
--  Приглашения
-- -----------------------------------------------------------------------------
CREATE TABLE hire.invitations (
    id             uuid PRIMARY KEY DEFAULT uuidv7(),
    company_id     uuid NOT NULL REFERENCES hire.companies (id) ON DELETE CASCADE,
    sender_user_id uuid REFERENCES core.users (id) ON DELETE SET NULL,
    candidate_id   uuid NOT NULL REFERENCES profile.candidates (user_id) ON DELETE CASCADE,
    -- приглашение не обязано ссылаться на вакансию (п. 2.1 ТЗ); SET NULL,
    -- потому что факт приглашения должен пережить архивацию вакансии
    vacancy_id     uuid REFERENCES hire.vacancies (id) ON DELETE SET NULL,
    need_id        uuid REFERENCES hire.employer_needs (id) ON DELETE SET NULL,

    message        text NOT NULL
                       CONSTRAINT invitations_message_len CHECK (char_length(message) BETWEEN 1 AND 5000),
    -- вилка обязательна: кандидат видит условия до начала общения
    salary_min     integer NOT NULL CHECK (salary_min > 0 AND salary_min <= 100000000),
    salary_max     integer NOT NULL CHECK (salary_max > 0 AND salary_max <= 100000000),
    currency       char(3) NOT NULL DEFAULT 'RUB' CHECK (currency ~ '^[A-Z]{3}$'),
    -- способ связи со стороны работодателя
    contact_method hire.contact_method NOT NULL DEFAULT 'platform',
    contact_value  varchar(120),

    status         hire.invitation_status NOT NULL DEFAULT 'sent',
    decline_reason varchar(300),
    sent_at        timestamptz NOT NULL DEFAULT now(),
    viewed_at      timestamptz,
    responded_at   timestamptz,
    expires_at     timestamptz,

    CONSTRAINT invitations_salary_range   CHECK (salary_max >= salary_min),
    CONSTRAINT invitations_viewed_after_sent CHECK (viewed_at IS NULL OR viewed_at >= sent_at),
    CONSTRAINT invitations_responded_after_sent CHECK (responded_at IS NULL OR responded_at >= sent_at),
    CONSTRAINT invitations_final_has_response
        CHECK (status NOT IN ('accepted', 'declined') OR responded_at IS NOT NULL),
    CONSTRAINT invitations_contact_value_present
        CHECK (contact_method = 'platform' OR char_length(btrim(coalesce(contact_value, ''))) > 0)
);

COMMENT ON TABLE hire.invitations IS 'Выход работодателя на контакт с конкретным кандидатом — основная механика сервиса';

-- Журнал смены статусов: обе стороны видят актуальный статус и его историю
CREATE TABLE hire.invitation_events (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invitation_id  uuid NOT NULL REFERENCES hire.invitations (id) ON DELETE CASCADE,
    from_status    hire.invitation_status,
    to_status      hire.invitation_status NOT NULL,
    actor_user_id  uuid REFERENCES core.users (id) ON DELETE SET NULL,
    comment        varchar(300),
    created_at     timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT invitation_events_status_changed CHECK (from_status IS DISTINCT FROM to_status)
);

-- -----------------------------------------------------------------------------
--  Отклики кандидата (дополнительный сценарий, п. 6.6 ТЗ)
-- -----------------------------------------------------------------------------
CREATE TABLE hire.applications (
    id           uuid PRIMARY KEY DEFAULT uuidv7(),
    vacancy_id   uuid NOT NULL REFERENCES hire.vacancies (id) ON DELETE CASCADE,
    candidate_id uuid NOT NULL REFERENCES profile.candidates (user_id) ON DELETE CASCADE,
    cover_letter text CONSTRAINT applications_cover_letter_len CHECK (char_length(cover_letter) <= 5000),
    status       hire.application_status NOT NULL DEFAULT 'sent',
    created_at   timestamptz NOT NULL DEFAULT now(),
    viewed_at    timestamptz,
    responded_at timestamptz,

    CONSTRAINT applications_unique UNIQUE (vacancy_id, candidate_id)
);

CREATE TABLE hire.application_events (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    application_id uuid NOT NULL REFERENCES hire.applications (id) ON DELETE CASCADE,
    from_status    hire.application_status,
    to_status      hire.application_status NOT NULL,
    actor_user_id  uuid REFERENCES core.users (id) ON DELETE SET NULL,
    comment        varchar(300),
    created_at     timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT application_events_status_changed CHECK (from_status IS DISTINCT FROM to_status)
);

-- -----------------------------------------------------------------------------
--  Раскрытие контактов
-- -----------------------------------------------------------------------------
--  «Работодатель не видит контактные данные кандидата до момента, когда
--  кандидат принял приглашение или откликнулся сам» — здесь это проверяемый
--  факт в БД, а не условие в коде: нет строки — бэкенд не отдаёт контакты.
--  Основание всегда конкретно: ровно одна из двух ссылок заполнена.
-- -----------------------------------------------------------------------------
CREATE TABLE hire.contact_disclosures (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    candidate_id   uuid NOT NULL REFERENCES profile.candidates (user_id) ON DELETE CASCADE,
    company_id     uuid NOT NULL REFERENCES hire.companies (id) ON DELETE CASCADE,
    reason         hire.disclosure_reason NOT NULL,
    invitation_id  uuid REFERENCES hire.invitations (id) ON DELETE CASCADE,
    application_id uuid REFERENCES hire.applications (id) ON DELETE CASCADE,
    granted_at     timestamptz NOT NULL DEFAULT now(),
    revoked_at     timestamptz,

    CONSTRAINT contact_disclosures_source_matches_reason CHECK (
           (reason = 'invitation_accepted' AND invitation_id IS NOT NULL AND application_id IS NULL)
        OR (reason = 'application_sent'    AND application_id IS NOT NULL AND invitation_id IS NULL)
    ),
    CONSTRAINT contact_disclosures_revoked_after_granted
        CHECK (revoked_at IS NULL OR revoked_at >= granted_at)
);

CREATE UNIQUE INDEX contact_disclosures_active_uq
    ON hire.contact_disclosures (candidate_id, company_id)
    WHERE revoked_at IS NULL;

-- =============================================================================
--  60. match — векторный поиск, подборки, объяснимость, чат
-- =============================================================================
--  Конвейер подбора: префильтр по категории и жёстким условиям -> сортировка
--  по косинусной близости эмбеддингов -> точный отбор LLM с текстовым
--  обоснованием. Каждая стадия оставляет след в search_results, поэтому выдачу
--  можно объяснить работодателю и проверить на собственной процедуре валидации.
--
--  Уточнение запроса в чате не перезаписывает подборку, а порождает новый
--  прогон с parent_run_id — «фильтрация и уточнение без потери ранее
--  полученной подборки» (п. 2.1 ТЗ).
-- =============================================================================

CREATE TYPE match.match_stage  AS ENUM ('prefilter', 'vector', 'llm');
CREATE TYPE match.run_status   AS ENUM ('queued', 'running', 'done', 'failed');
CREATE TYPE match.message_role AS ENUM ('user', 'assistant', 'system', 'tool');

-- -----------------------------------------------------------------------------
--  Эмбеддинги
-- -----------------------------------------------------------------------------
--  Таблица на каждую сущность, а не одна полиморфная: полиморфная не даёт
--  поставить внешний ключ, а без него осиротевшие вектора накапливаются молча.
--  Размерность 1024 — под multilingual-e5-large / bge-m3 (русский + английский).
--  source_hash позволяет не пересчитывать вектор, если исходный текст не менялся.
-- -----------------------------------------------------------------------------
CREATE TABLE match.candidate_embeddings (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    candidate_id uuid NOT NULL REFERENCES profile.candidates (user_id) ON DELETE CASCADE,
    model        varchar(60) NOT NULL,
    embedding    vector(1024) NOT NULL,
    source_hash  varchar(64) NOT NULL,
    built_at     timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT candidate_embeddings_uq UNIQUE (candidate_id, model)
);

CREATE TABLE match.vacancy_embeddings (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    vacancy_id  uuid NOT NULL REFERENCES hire.vacancies (id) ON DELETE CASCADE,
    model       varchar(60) NOT NULL,
    embedding   vector(1024) NOT NULL,
    source_hash varchar(64) NOT NULL,
    built_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT vacancy_embeddings_uq UNIQUE (vacancy_id, model)
);

CREATE TABLE match.need_embeddings (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    need_id     uuid NOT NULL REFERENCES hire.employer_needs (id) ON DELETE CASCADE,
    model       varchar(60) NOT NULL,
    embedding   vector(1024) NOT NULL,
    source_hash varchar(64) NOT NULL,
    built_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT need_embeddings_uq UNIQUE (need_id, model)
);

-- -----------------------------------------------------------------------------
--  Подборка
-- -----------------------------------------------------------------------------
CREATE TABLE match.search_runs (
    id             uuid PRIMARY KEY DEFAULT uuidv7(),
    need_id        uuid NOT NULL REFERENCES hire.employer_needs (id) ON DELETE CASCADE,
    author_user_id uuid REFERENCES core.users (id) ON DELETE SET NULL,
    -- уточнение из чата: новый прогон, ссылающийся на предыдущий
    parent_run_id  uuid REFERENCES match.search_runs (id) ON DELETE SET NULL,
    strategy       varchar(40) NOT NULL DEFAULT 'prefilter_vector_llm',
    -- фактические параметры прогона: пороги, размер пула, доля случайных резюме
    params         jsonb,
    status         match.run_status NOT NULL DEFAULT 'queued',
    pool_size      integer CHECK (pool_size >= 0),
    llm_model      varchar(60),
    summary        text CONSTRAINT search_runs_summary_len CHECK (char_length(summary) <= 10000),
    error_message  varchar(500),
    created_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,

    CONSTRAINT search_runs_no_self_parent CHECK (parent_run_id IS DISTINCT FROM id),
    CONSTRAINT search_runs_failed_has_error CHECK (status <> 'failed' OR error_message IS NOT NULL)
);

COMMENT ON COLUMN match.search_runs.summary IS 'Обзор подборки от LLM: то, что работодатель обсуждает в чате';

CREATE TABLE match.search_results (
    run_id       uuid NOT NULL REFERENCES match.search_runs (id) ON DELETE CASCADE,
    candidate_id uuid NOT NULL REFERENCES profile.candidates (user_id) ON DELETE CASCADE,
    -- до какой стадии конвейера дошёл кандидат
    stage        match.match_stage NOT NULL,
    rank         smallint CHECK (rank > 0),
    vector_score numeric(6, 5) CHECK (vector_score BETWEEN -1 AND 1),
    llm_score    numeric(6, 5) CHECK (llm_score BETWEEN 0 AND 1),
    final_score  numeric(6, 5) CHECK (final_score BETWEEN 0 AND 1),
    -- краткая объяснимость выдачи — требование п. 2.1 ТЗ
    explanation  varchar(2000),
    is_shown     boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (run_id, candidate_id),
    CONSTRAINT search_results_llm_stage_has_explanation
        CHECK (stage <> 'llm' OR explanation IS NOT NULL),
    CONSTRAINT search_results_rank_uq UNIQUE (run_id, rank)
);

-- -----------------------------------------------------------------------------
--  Чат над подборкой
-- -----------------------------------------------------------------------------
CREATE TABLE match.chat_threads (
    id         uuid PRIMARY KEY DEFAULT uuidv7(),
    need_id    uuid NOT NULL REFERENCES hire.employer_needs (id) ON DELETE CASCADE,
    user_id    uuid NOT NULL REFERENCES core.users (id) ON DELETE CASCADE,
    title      varchar(120),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE match.chat_messages (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    thread_id    uuid NOT NULL REFERENCES match.chat_threads (id) ON DELETE CASCADE,
    -- прогон, который этот ход породил или на который опирался
    run_id       uuid REFERENCES match.search_runs (id) ON DELETE SET NULL,
    role         match.message_role NOT NULL,
    content      text NOT NULL
                     CONSTRAINT chat_messages_content_len CHECK (char_length(content) BETWEEN 1 AND 20000),
    tool_payload jsonb,
    tokens       integer CHECK (tokens >= 0),
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- =============================================================================
--  70. Индексы
-- =============================================================================
--  PostgreSQL сам создаёт индексы под PRIMARY KEY и UNIQUE, но не под внешние
--  ключи. Здесь покрыты FK, по которым реально идут джойны и фильтры, плюс
--  индексы под конкретные сценарии обоих кабинетов. Партиальные индексы
--  предпочтены полным там, где запрос всегда идёт с тем же условием.
-- =============================================================================

-- --- core --------------------------------------------------------------------
CREATE INDEX users_status_idx             ON core.users (status);
CREATE INDEX user_roles_role_idx          ON core.user_roles (role);
CREATE INDEX email_tokens_user_idx        ON core.email_tokens (user_id);
-- выборка живых токенов при подтверждении адреса
CREATE INDEX email_tokens_active_idx      ON core.email_tokens (user_id, purpose) WHERE used_at IS NULL;
CREATE INDEX refresh_sessions_user_idx    ON core.refresh_sessions (user_id) WHERE revoked_at IS NULL;
CREATE INDEX user_consents_user_idx       ON core.user_consents (user_id);

-- --- ref ---------------------------------------------------------------------
CREATE INDEX specializations_industry_idx ON ref.specializations (industry_id);
CREATE INDEX specializations_parent_idx   ON ref.specializations (parent_id);
CREATE INDEX grades_industry_idx          ON ref.grades (industry_id);
CREATE INDEX categories_grade_idx         ON ref.categories (grade_id);
CREATE INDEX skills_area_idx              ON ref.skills (area_id);
CREATE INDEX skills_kind_idx              ON ref.skills (kind);
-- автодополнение по названию навыка
CREATE INDEX skills_name_trgm_idx         ON ref.skills USING gin (name gin_trgm_ops);
CREATE INDEX skill_aliases_skill_idx      ON ref.skill_aliases (skill_id);
CREATE INDEX specialization_skills_skill_idx ON ref.specialization_skills (skill_id);
CREATE INDEX attribute_defs_industry_idx  ON ref.attribute_defs (industry_id);
CREATE INDEX attribute_options_attr_idx   ON ref.attribute_options (attribute_id);

-- --- profile -----------------------------------------------------------------
-- главный путь банка кандидатов: категория + готовность к предложениям
CREATE INDEX candidates_category_idx      ON profile.candidates (current_category_id) WHERE is_open_to_offers;
-- ранжирование внутри категории по силе подтверждённого профиля
CREATE INDEX candidates_category_strength_idx
    ON profile.candidates (current_category_id, profile_strength DESC);
CREATE INDEX candidates_city_idx          ON profile.candidates (city);
CREATE INDEX candidates_search_idx        ON profile.candidates USING gin (search_vector);

CREATE INDEX candidate_links_candidate_idx ON profile.candidate_links (candidate_id);
-- обратный проход: «кто владеет навыком X на уровне не ниже Y»
CREATE INDEX candidate_skills_skill_idx   ON profile.candidate_skills (skill_id, confirmed_level DESC NULLS LAST);
CREATE INDEX candidate_team_roles_role_idx ON profile.candidate_team_roles (team_role_id);
CREATE INDEX cav_candidate_idx            ON profile.candidate_attribute_values (candidate_id);
CREATE INDEX cav_attribute_idx            ON profile.candidate_attribute_values (attribute_id);

CREATE INDEX resume_documents_candidate_idx ON profile.resume_documents (candidate_id, uploaded_at DESC);
CREATE INDEX resume_documents_pending_idx   ON profile.resume_documents (parse_status) WHERE parse_status IN ('pending', 'processing');
CREATE INDEX resume_parse_results_doc_idx   ON profile.resume_parse_results (document_id);

CREATE INDEX fsp_profiles_status_idx      ON profile.fsp_profiles (verification_status);
CREATE INDEX fsp_achievements_candidate_idx ON profile.fsp_achievements (candidate_id);
CREATE INDEX fsp_achievements_event_idx   ON profile.fsp_achievements (event_id);
CREATE INDEX fsp_events_level_idx         ON profile.fsp_events (level, starts_on DESC);

CREATE INDEX github_repos_candidate_idx   ON profile.github_repos (candidate_id);
CREATE INDEX github_signals_skill_idx     ON profile.github_signals (skill_id);

-- --- assess ------------------------------------------------------------------
CREATE INDEX survey_questions_survey_idx  ON assess.survey_questions (survey_id);
CREATE INDEX survey_options_question_idx  ON assess.survey_options (question_id);
CREATE INDEX survey_sessions_candidate_idx ON assess.survey_sessions (candidate_id, started_at DESC);
CREATE INDEX survey_answers_session_idx   ON assess.survey_answers (session_id);

-- подбор шаблонов под генерацию: специализация + грейд + активность
CREATE INDEX task_templates_target_idx    ON assess.task_templates (specialization_id, grade_id) WHERE is_active;
CREATE INDEX task_templates_skill_idx     ON assess.task_templates (skill_id);
CREATE INDEX tasks_template_idx           ON assess.tasks (template_id);
CREATE INDEX tasks_candidate_idx          ON assess.tasks (candidate_id, created_at DESC);

CREATE INDEX test_sessions_candidate_idx  ON assess.test_sessions (candidate_id, created_at DESC);
-- фоновая задача «просрочить зависшие сессии»
CREATE INDEX test_sessions_open_idx       ON assess.test_sessions (deadline_at) WHERE status IN ('created', 'in_progress');
CREATE INDEX task_answers_session_idx     ON assess.task_answers (session_id);
CREATE INDEX task_answers_unchecked_idx   ON assess.task_answers (submitted_at) WHERE checked_at IS NULL;

CREATE INDEX grade_assignments_candidate_idx ON assess.grade_assignments (candidate_id, assigned_at DESC);
CREATE INDEX grade_assignments_category_idx  ON assess.grade_assignments (category_id) WHERE is_current;
CREATE INDEX regular_assignments_candidate_idx ON assess.regular_assignments (candidate_id, issued_at DESC);
CREATE INDEX regular_assignments_open_idx    ON assess.regular_assignments (due_at) WHERE status IN ('issued', 'viewed');
CREATE INDEX regular_assignments_company_idx ON assess.regular_assignments (company_id);

-- --- hire --------------------------------------------------------------------
CREATE INDEX employers_company_idx        ON hire.employers (company_id);
CREATE INDEX vacancies_company_idx        ON hire.vacancies (company_id, created_at DESC);
-- витрина опубликованных вакансий по категориям
CREATE INDEX vacancies_published_idx      ON hire.vacancies (category_id, published_at DESC) WHERE status = 'published';
CREATE INDEX vacancies_search_idx         ON hire.vacancies USING gin (search_vector);
CREATE INDEX vacancy_skills_skill_idx     ON hire.vacancy_skills (skill_id);

CREATE INDEX employer_needs_company_idx   ON hire.employer_needs (company_id, created_at DESC) WHERE is_active;
CREATE INDEX need_skills_skill_idx        ON hire.need_skills (skill_id);

-- лента входящих приглашений в кабинете кандидата
CREATE INDEX invitations_candidate_idx    ON hire.invitations (candidate_id, status, sent_at DESC);
-- отслеживание статусов в кабинете работодателя
CREATE INDEX invitations_company_idx      ON hire.invitations (company_id, status, sent_at DESC);
CREATE INDEX invitations_vacancy_idx      ON hire.invitations (vacancy_id);
CREATE INDEX invitations_need_idx         ON hire.invitations (need_id);
CREATE INDEX invitation_events_invitation_idx ON hire.invitation_events (invitation_id, created_at);

CREATE INDEX applications_candidate_idx   ON hire.applications (candidate_id, created_at DESC);
CREATE INDEX applications_vacancy_idx     ON hire.applications (vacancy_id, status);
CREATE INDEX application_events_application_idx ON hire.application_events (application_id, created_at);

CREATE INDEX contact_disclosures_company_idx ON hire.contact_disclosures (company_id);

-- --- match -------------------------------------------------------------------
-- HNSW по косинусу: быстрый ANN-поиск ближайших резюме к описанию потребности.
-- m/ef_construction по умолчанию (16/64) — компромисс для масштаба MVP.
CREATE INDEX candidate_embeddings_hnsw_idx ON match.candidate_embeddings
    USING hnsw (embedding vector_cosine_ops);
CREATE INDEX vacancy_embeddings_hnsw_idx   ON match.vacancy_embeddings
    USING hnsw (embedding vector_cosine_ops);
CREATE INDEX need_embeddings_hnsw_idx      ON match.need_embeddings
    USING hnsw (embedding vector_cosine_ops);

CREATE INDEX search_runs_need_idx          ON match.search_runs (need_id, created_at DESC);
CREATE INDEX search_runs_parent_idx        ON match.search_runs (parent_run_id);
-- «в какие подборки попадал этот кандидат»
CREATE INDEX search_results_candidate_idx  ON match.search_results (candidate_id);
CREATE INDEX search_results_top_idx        ON match.search_results (run_id, final_score DESC) WHERE is_shown;

CREATE INDEX chat_threads_need_idx         ON match.chat_threads (need_id, updated_at DESC);
CREATE INDEX chat_messages_thread_idx      ON match.chat_messages (thread_id, created_at);

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

-- =============================================================================
--  91. Банк заданий
-- =============================================================================
--  Отдельный файл не случайно: банк — это сменная часть системы. Чтобы
--  запустить платформу в другой нише, достаточно заменить этот файл набором
--  шаблонов для своих специализаций и грейдов; остальная схема не меняется.
--
--  Шаблон несёт сложность, ожидаемое время, рубрику проверки и промт
--  генератора. Сравнимость кандидатов берётся из шаблона, уникальность
--  формулировки — из генерации экземпляра (assess.tasks).
-- =============================================================================

INSERT INTO assess.task_templates (
    specialization_id, grade_id, skill_id, code, title, format,
    difficulty, expected_minutes, max_score, generator_prompt, grading_rubric, source
)
SELECT sp.id, g.id, sk.id, t.code, t.title, t.format::assess.question_format,
       t.difficulty, t.minutes, t.max_score, t.gen_prompt, t.rubric, 'manual'
FROM (VALUES
    ('it', 'backend', 'junior', 'postgresql', 'be_jun_sql_join',
     'Выборка с соединением и агрегацией', 'code', 3, 20, 10.0,
     'Сгенерируй задачу на SQL-выборку с одним JOIN и агрегацией. Предметная область — случайная из списка: доставка, библиотека, автосервис, стриминг. Схема из 2-3 таблиц приводится в условии. Ответ — один запрос.',
     'Корректность результата — 6 баллов; отсутствие лишних подзапросов — 2; читаемость (алиасы, форматирование) — 2.'),

    ('it', 'backend', 'middle', 'rest_api', 'be_mid_api_design',
     'Проектирование REST-ресурса', 'open', 5, 25, 10.0,
     'Опиши продуктовое требование и попроси спроектировать REST-ресурс: пути, методы, коды ответов, идемпотентность, пагинация. Домен выбирай случайно, объём требования — 5-7 предложений.',
     'Корректные коды ответов — 3; идемпотентность изменяющих операций — 3; пагинация и фильтрация — 2; версионирование и обработка ошибок — 2.'),

    ('it', 'backend', 'middle', 'db_design', 'be_mid_schema_fix',
     'Поиск дефектов в схеме БД', 'open', 6, 30, 12.0,
     'Приведи DDL на 3-4 таблицы с 3-4 намеренными дефектами (отсутствие FK, денормализация без причины, тип данных не по смыслу, пропущенный уникальный индекс). Попроси найти дефекты и предложить исправление.',
     'По 3 балла за каждый найденный дефект с корректным обоснованием; максимум 12.'),

    ('it', 'backend', 'senior', 'db_design', 'be_sen_concurrency',
     'Конкурентный доступ и блокировки', 'case', 8, 35, 15.0,
     'Опиши сценарий гонки при одновременном изменении общего ресурса (остатки на складе, бронирование мест). Попроси выбрать уровень изоляции и стратегию блокировки с обоснованием.',
     'Диагноз гонки — 5; корректный уровень изоляции с обоснованием — 5; работоспособность решения под нагрузкой — 5.'),

    ('it', 'frontend', 'junior', 'react', 'fe_jun_state',
     'Состояние компонента', 'code', 3, 20, 10.0,
     'Сгенерируй задачу на управление локальным состоянием в React: форма с валидацией одного поля и блокировкой кнопки. Требование формулируй как пользовательский сценарий.',
     'Работающая валидация — 5; отсутствие лишних ре-рендеров — 3; доступность (label, aria) — 2.'),

    ('it', 'frontend', 'middle', 'typescript', 'fe_mid_types',
     'Типизация внешнего контракта', 'code', 5, 25, 10.0,
     'Дай JSON-ответ внешнего API со вложенностью и необязательными полями. Попроси описать типы и безопасный разбор без any.',
     'Отсутствие any — 4; корректная необязательность полей — 3; обработка несоответствия контракту — 3.'),

    ('it', 'ml_engineer', 'middle', 'rag', 'ml_mid_rag',
     'Выбор стратегии RAG', 'case', 6, 30, 12.0,
     'Опиши корпус документов (объём, язык, структуру) и требование к качеству ответов. Попроси выбрать стратегию разбиения, модель эмбеддингов и способ оценки качества.',
     'Обоснование чанкинга — 4; выбор модели под язык корпуса — 4; измеримые метрики качества — 4.'),

    ('it', 'devops', 'middle', 'kubernetes', 'do_mid_k8s_debug',
     'Диагностика падающего пода', 'case', 6, 25, 12.0,
     'Приведи вывод kubectl describe/logs с одной из причин отказа (OOMKilled, CrashLoopBackOff из-за конфига, неготовый readiness). Попроси поставить диагноз и предложить исправление.',
     'Верный диагноз — 6; исправление, устраняющее причину, а не симптом — 4; профилактика повторения — 2.'),

    ('it', 'qa', 'junior', 'test_design', 'qa_jun_testdesign',
     'Тест-дизайн по требованию', 'open', 3, 20, 10.0,
     'Дай короткое требование к форме или API-методу. Попроси составить набор проверок: классы эквивалентности, граничные значения, негативные сценарии.',
     'Классы эквивалентности — 4; границы — 3; негативные сценарии — 3.'),

    ('marketing', 'smm', 'junior', 'content_plan', 'mkt_jun_content',
     'Контент-план на неделю', 'open', 3, 20, 10.0,
     'Опиши продукт и аудиторию. Попроси составить контент-план на неделю с форматами и целевыми метриками.',
     'Соответствие аудитории — 4; разнообразие форматов — 3; измеримые метрики — 3.')
) AS t(industry_code, spec_code, grade_code, skill_code, code, title, format,
       difficulty, minutes, max_score, gen_prompt, rubric)
JOIN ref.industries i      ON i.code  = t.industry_code
JOIN ref.specializations sp ON sp.code = t.spec_code  AND sp.industry_id = i.id
JOIN ref.grades g          ON g.code  = t.grade_code AND g.industry_id  = i.id
LEFT JOIN ref.skills sk    ON sk.code = t.skill_code;

-- =============================================================================
--  12. Настройка подключения
-- =============================================================================
--  Схемы попадают в search_path текущей роли, чтобы в psql и в ad-hoc запросах
--  не приходилось квалифицировать каждое имя. public оставлен первым: новые
--  объекты, созданные без указания схемы, не должны случайно падать в core.
--
--  На модели бэкенда это не распространяется — там схема указывается явно
--  (__table_args__ = {"schema": ...}), иначе ORM сгенерирует DDL мимо модуля.
-- -----------------------------------------------------------------------------
ALTER ROLE CURRENT_USER
    SET search_path = public, core, ref, profile, assess, hire, match;

COMMIT;
