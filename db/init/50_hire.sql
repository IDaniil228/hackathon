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
