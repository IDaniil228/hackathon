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
