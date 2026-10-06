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
