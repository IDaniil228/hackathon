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
