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
