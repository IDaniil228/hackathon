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
