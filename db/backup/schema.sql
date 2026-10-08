-- =============================================================================
--  Платформа ФСП — снимок схемы базы данных
-- =============================================================================
--  Файл СГЕНЕРИРОВАН из моделей SQLAlchemy командой `fspdb dump-schema`.
--  Править его руками бессмысленно: источник правды — app/db/models,
--  а схему в базе разворачивает `alembic upgrade head`.
--
--  Это бэкап и материал для чтения: удобно открыть одним файлом, показать
--  на защите и положить рядом с документацией.
--  Сгенерирован: 2026-10-06T16:30:25+00:00
-- =============================================================================

BEGIN;

-- Схемы
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS ref;
CREATE SCHEMA IF NOT EXISTS profile;
CREATE SCHEMA IF NOT EXISTS assess;
CREATE SCHEMA IF NOT EXISTS hire;
CREATE SCHEMA IF NOT EXISTS match;

-- Расширения
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ENUM-типы предметной области
CREATE TYPE core.user_status AS ENUM ('pending', 'active', 'blocked');
CREATE TYPE core.user_role AS ENUM ('candidate', 'employer', 'admin');
CREATE TYPE core.token_purpose AS ENUM ('verify_email', 'reset_password');
CREATE TYPE core.consent_kind AS ENUM ('pd_processing', 'profile_publication', 'fsp_data_sync');
CREATE TYPE ref.skill_kind AS ENUM ('language', 'framework', 'tool', 'practice', 'soft', 'domain');
CREATE TYPE ref.attribute_scope AS ENUM ('candidate', 'vacancy', 'company', 'task');
CREATE TYPE ref.attribute_data_type AS ENUM ('string', 'text', 'integer', 'decimal', 'boolean', 'date', 'option');
CREATE TYPE profile.work_format AS ENUM ('office', 'hybrid', 'remote');
CREATE TYPE profile.employment_type AS ENUM ('full_time', 'part_time', 'project', 'internship', 'contract');
CREATE TYPE profile.link_kind AS ENUM ('github', 'gitlab', 'telegram', 'portfolio', 'linkedin', 'habr', 'other');
CREATE TYPE profile.skill_source AS ENUM ('self', 'resume', 'test', 'github', 'fsp');
CREATE TYPE profile.resume_kind AS ENUM ('uploaded', 'generated');
CREATE TYPE profile.parse_status AS ENUM ('pending', 'processing', 'done', 'failed');
CREATE TYPE profile.fsp_link_status AS ENUM ('unlinked', 'pending', 'verified', 'failed');
CREATE TYPE profile.fsp_event_level AS ENUM ('local', 'regional', 'federal', 'international');
CREATE TYPE profile.achievement_source AS ENUM ('registry', 'manual');
CREATE TYPE assess.question_format AS ENUM ('single_choice', 'multi_choice', 'open', 'code', 'case', 'scale');
CREATE TYPE assess.template_source AS ENUM ('manual', 'llm', 'imported');
CREATE TYPE assess.session_status AS ENUM ('created', 'in_progress', 'submitted', 'scored', 'expired', 'cancelled');
CREATE TYPE assess.session_outcome AS ENUM ('passed', 'failed', 'downgraded', 'upgraded');
CREATE TYPE assess.checked_by AS ENUM ('auto', 'llm', 'human');
CREATE TYPE assess.grade_source AS ENUM ('test', 'manual', 'admin', 'import');
CREATE TYPE assess.assignment_status AS ENUM ('issued', 'viewed', 'answered', 'scored', 'expired', 'skipped');
CREATE TYPE hire.company_size AS ENUM ('micro', 'small', 'medium', 'large', 'enterprise');
CREATE TYPE hire.vacancy_status AS ENUM ('draft', 'published', 'paused', 'archived');
CREATE TYPE hire.skill_importance AS ENUM ('required', 'nice_to_have');
CREATE TYPE hire.invitation_status AS ENUM ('sent', 'viewed', 'accepted', 'declined', 'withdrawn', 'expired');
CREATE TYPE hire.application_status AS ENUM ('sent', 'viewed', 'invited', 'rejected', 'withdrawn');
CREATE TYPE hire.contact_method AS ENUM ('email', 'phone', 'telegram', 'platform');
CREATE TYPE hire.disclosure_reason AS ENUM ('invitation_accepted', 'application_sent');
CREATE TYPE match.match_stage AS ENUM ('prefilter', 'vector', 'llm');
CREATE TYPE match.run_status AS ENUM ('queued', 'running', 'done', 'failed');
CREATE TYPE match.message_role AS ENUM ('user', 'assistant', 'system', 'tool');

-- Таблицы и ограничения

CREATE TABLE core.users (
	id UUID DEFAULT uuidv7() NOT NULL, 
	email CITEXT NOT NULL, 
	password_hash VARCHAR(255), 
	status core.user_status DEFAULT 'pending' NOT NULL, 
	email_verified_at TIMESTAMP WITH TIME ZONE, 
	external_subject VARCHAR(64), 
	last_login_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT users_pkey PRIMARY KEY (id), 
	CONSTRAINT users_email_len CHECK (char_length(email) BETWEEN 5 AND 254), 
	CONSTRAINT users_email_format CHECK (email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[A-Za-z]{2,}$'), 
	CONSTRAINT users_auth_method_present CHECK (password_hash IS NOT NULL OR external_subject IS NOT NULL), 
	CONSTRAINT users_active_requires_verified_email CHECK (status <> 'active' OR email_verified_at IS NOT NULL), 
	CONSTRAINT users_email_key UNIQUE (email), 
	CONSTRAINT users_external_subject_key UNIQUE (external_subject)
);

CREATE TABLE profile.fsp_events (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	external_id VARCHAR(64), 
	title VARCHAR(200) NOT NULL, 
	discipline VARCHAR(100), 
	level profile.fsp_event_level NOT NULL, 
	starts_on DATE, 
	ends_on DATE, 
	CONSTRAINT fsp_events_pkey PRIMARY KEY (id), 
	CONSTRAINT fsp_events_title_not_blank CHECK (char_length(btrim(title)) > 0), 
	CONSTRAINT fsp_events_period CHECK (ends_on IS NULL OR starts_on IS NULL OR ends_on >= starts_on), 
	CONSTRAINT fsp_events_external_id_key UNIQUE (external_id)
);

CREATE TABLE ref.industries (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	code VARCHAR(40) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	description VARCHAR(500), 
	is_active BOOLEAN DEFAULT 'true' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT industries_pkey PRIMARY KEY (id), 
	CONSTRAINT industries_code_format CHECK (code ~ '^[a-z][a-z0-9_]{1,39}$'), 
	CONSTRAINT industries_name_not_blank CHECK (char_length(btrim(name)) > 0), 
	CONSTRAINT industries_code_key UNIQUE (code)
);

CREATE TABLE ref.skill_areas (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	parent_id BIGINT, 
	code VARCHAR(40) NOT NULL, 
	name VARCHAR(80) NOT NULL, 
	sort_order SMALLINT DEFAULT '0' NOT NULL, 
	CONSTRAINT skill_areas_pkey PRIMARY KEY (id), 
	CONSTRAINT skill_areas_code_format CHECK (code ~ '^[a-z][a-z0-9_]{1,39}$'), 
	CONSTRAINT skill_areas_name_not_blank CHECK (char_length(btrim(name)) > 0), 
	CONSTRAINT skill_areas_no_self_parent CHECK (parent_id IS DISTINCT FROM id), 
	CONSTRAINT skill_areas_parent_id_fkey FOREIGN KEY(parent_id) REFERENCES ref.skill_areas (id) ON DELETE RESTRICT, 
	CONSTRAINT skill_areas_code_key UNIQUE (code)
);

CREATE TABLE assess.survey_templates (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	industry_id BIGINT NOT NULL, 
	code VARCHAR(40) NOT NULL, 
	title VARCHAR(120) NOT NULL, 
	version SMALLINT DEFAULT '1' NOT NULL, 
	is_active BOOLEAN DEFAULT 'true' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT survey_templates_pkey PRIMARY KEY (id), 
	CONSTRAINT survey_templates_version_positive CHECK (version > 0), 
	CONSTRAINT survey_templates_industry_id_fkey FOREIGN KEY(industry_id) REFERENCES ref.industries (id) ON DELETE CASCADE, 
	CONSTRAINT survey_templates_code_key UNIQUE (code)
);

CREATE TABLE core.email_tokens (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	user_id UUID NOT NULL, 
	purpose core.token_purpose NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	used_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT email_tokens_pkey PRIMARY KEY (id), 
	CONSTRAINT email_tokens_hash_format CHECK (token_hash ~ '^[a-f0-9]{64}$'), 
	CONSTRAINT email_tokens_expires_after_create CHECK (expires_at > created_at), 
	CONSTRAINT email_tokens_used_after_create CHECK (used_at IS NULL OR used_at >= created_at), 
	CONSTRAINT email_tokens_user_id_fkey FOREIGN KEY(user_id) REFERENCES core.users (id) ON DELETE CASCADE, 
	CONSTRAINT email_tokens_token_hash_key UNIQUE (token_hash)
);

CREATE TABLE core.refresh_sessions (
	id UUID DEFAULT uuidv7() NOT NULL, 
	user_id UUID NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	user_agent VARCHAR(255), 
	ip INET, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT refresh_sessions_pkey PRIMARY KEY (id), 
	CONSTRAINT refresh_sessions_hash_format CHECK (token_hash ~ '^[a-f0-9]{64}$'), 
	CONSTRAINT refresh_sessions_expires_after_create CHECK (expires_at > created_at), 
	CONSTRAINT refresh_sessions_user_id_fkey FOREIGN KEY(user_id) REFERENCES core.users (id) ON DELETE CASCADE, 
	CONSTRAINT refresh_sessions_token_hash_key UNIQUE (token_hash)
);

CREATE TABLE core.user_consents (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	user_id UUID NOT NULL, 
	kind core.consent_kind NOT NULL, 
	document_version VARCHAR(20) NOT NULL, 
	granted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT user_consents_pkey PRIMARY KEY (id), 
	CONSTRAINT user_consents_doc_version_not_blank CHECK (char_length(btrim(document_version)) > 0), 
	CONSTRAINT user_consents_revoked_after_granted CHECK (revoked_at IS NULL OR revoked_at >= granted_at), 
	CONSTRAINT user_consents_user_id_fkey FOREIGN KEY(user_id) REFERENCES core.users (id) ON DELETE CASCADE
);

CREATE TABLE core.user_roles (
	user_id UUID NOT NULL, 
	role core.user_role NOT NULL, 
	granted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT user_roles_pkey PRIMARY KEY (user_id, role), 
	CONSTRAINT user_roles_user_id_fkey FOREIGN KEY(user_id) REFERENCES core.users (id) ON DELETE CASCADE
);

CREATE TABLE hire.companies (
	id UUID DEFAULT uuidv7() NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	legal_name VARCHAR(200), 
	inn VARCHAR(12), 
	industry_id BIGINT, 
	description TEXT, 
	website VARCHAR(2048), 
	logo_url VARCHAR(2048), 
	size hire.company_size, 
	city VARCHAR(100), 
	is_verified BOOLEAN DEFAULT 'false' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT companies_pkey PRIMARY KEY (id), 
	CONSTRAINT companies_name_not_blank CHECK (char_length(btrim(name)) > 0), 
	CONSTRAINT companies_inn_format CHECK (inn IS NULL OR inn ~ '^[0-9]{10}$' OR inn ~ '^[0-9]{12}$'), 
	CONSTRAINT companies_description_len CHECK (char_length(description) <= 5000), 
	CONSTRAINT companies_website_format CHECK (website IS NULL OR website ~ '^https?://[^[:space:]]{3,}$'), 
	CONSTRAINT companies_inn_key UNIQUE (inn), 
	CONSTRAINT companies_industry_id_fkey FOREIGN KEY(industry_id) REFERENCES ref.industries (id) ON DELETE SET NULL
);

CREATE TABLE ref.attribute_defs (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	scope ref.attribute_scope NOT NULL, 
	industry_id BIGINT, 
	code VARCHAR(50) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	data_type ref.attribute_data_type NOT NULL, 
	unit VARCHAR(20), 
	is_multi BOOLEAN DEFAULT 'false' NOT NULL, 
	is_required BOOLEAN DEFAULT 'false' NOT NULL, 
	is_public BOOLEAN DEFAULT 'true' NOT NULL, 
	sort_order SMALLINT DEFAULT '0' NOT NULL, 
	CONSTRAINT attribute_defs_pkey PRIMARY KEY (id), 
	CONSTRAINT attribute_defs_scope_code_uq UNIQUE (scope, code), 
	CONSTRAINT attribute_defs_id_type_uq UNIQUE (id, data_type), 
	CONSTRAINT attribute_defs_code_format CHECK (code ~ '^[a-z][a-z0-9_]{1,49}$'), 
	CONSTRAINT attribute_defs_name_not_blank CHECK (char_length(btrim(name)) > 0), 
	CONSTRAINT attribute_defs_multi_only_for_option CHECK (NOT is_multi OR data_type = 'option'), 
	CONSTRAINT attribute_defs_industry_id_fkey FOREIGN KEY(industry_id) REFERENCES ref.industries (id) ON DELETE CASCADE
);

CREATE TABLE ref.grades (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	industry_id BIGINT NOT NULL, 
	code VARCHAR(40) NOT NULL, 
	name VARCHAR(50) NOT NULL, 
	level SMALLINT NOT NULL, 
	min_experience_months SMALLINT, 
	cooldown_days SMALLINT DEFAULT '90' NOT NULL, 
	CONSTRAINT grades_pkey PRIMARY KEY (id), 
	CONSTRAINT grades_industry_code_uq UNIQUE (industry_id, code), 
	CONSTRAINT grades_industry_level_uq UNIQUE (industry_id, level), 
	CONSTRAINT grades_code_format CHECK (code ~ '^[a-z][a-z0-9_]{1,39}$'), 
	CONSTRAINT grades_name_not_blank CHECK (char_length(btrim(name)) > 0), 
	CONSTRAINT grades_level_range CHECK (level BETWEEN 0 AND 100), 
	CONSTRAINT grades_min_experience_range CHECK (min_experience_months IS NULL OR min_experience_months BETWEEN 0 AND 840), 
	CONSTRAINT grades_cooldown_range CHECK (cooldown_days BETWEEN 0 AND 3650), 
	CONSTRAINT grades_industry_id_fkey FOREIGN KEY(industry_id) REFERENCES ref.industries (id) ON DELETE RESTRICT
);

CREATE TABLE ref.skills (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	area_id BIGINT NOT NULL, 
	kind ref.skill_kind NOT NULL, 
	code VARCHAR(50) NOT NULL, 
	name VARCHAR(60) NOT NULL, 
	is_active BOOLEAN DEFAULT 'true' NOT NULL, 
	CONSTRAINT skills_pkey PRIMARY KEY (id), 
	CONSTRAINT skills_code_format CHECK (code ~ '^[a-z][a-z0-9_+.-]{1,49}$'), 
	CONSTRAINT skills_name_not_blank CHECK (char_length(btrim(name)) > 0), 
	CONSTRAINT skills_area_id_fkey FOREIGN KEY(area_id) REFERENCES ref.skill_areas (id) ON DELETE RESTRICT, 
	CONSTRAINT skills_code_key UNIQUE (code)
);

CREATE TABLE ref.specializations (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	industry_id BIGINT NOT NULL, 
	parent_id BIGINT, 
	code VARCHAR(40) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	description VARCHAR(500), 
	sort_order SMALLINT DEFAULT '0' NOT NULL, 
	is_active BOOLEAN DEFAULT 'true' NOT NULL, 
	CONSTRAINT specializations_pkey PRIMARY KEY (id), 
	CONSTRAINT specializations_industry_code_uq UNIQUE (industry_id, code), 
	CONSTRAINT specializations_code_format CHECK (code ~ '^[a-z][a-z0-9_]{1,39}$'), 
	CONSTRAINT specializations_name_not_blank CHECK (char_length(btrim(name)) > 0), 
	CONSTRAINT specializations_no_self_parent CHECK (parent_id IS DISTINCT FROM id), 
	CONSTRAINT specializations_industry_id_fkey FOREIGN KEY(industry_id) REFERENCES ref.industries (id) ON DELETE RESTRICT, 
	CONSTRAINT specializations_parent_id_fkey FOREIGN KEY(parent_id) REFERENCES ref.specializations (id) ON DELETE RESTRICT
);

CREATE TABLE assess.survey_questions (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	survey_id BIGINT NOT NULL, 
	order_no SMALLINT NOT NULL, 
	format assess.question_format NOT NULL, 
	text VARCHAR(500) NOT NULL, 
	is_required BOOLEAN DEFAULT 'true' NOT NULL, 
	CONSTRAINT survey_questions_pkey PRIMARY KEY (id), 
	CONSTRAINT survey_questions_order_uq UNIQUE (survey_id, order_no), 
	CONSTRAINT survey_questions_order_positive CHECK (order_no > 0), 
	CONSTRAINT survey_questions_text_not_blank CHECK (char_length(btrim(text)) > 0), 
	CONSTRAINT survey_questions_survey_id_fkey FOREIGN KEY(survey_id) REFERENCES assess.survey_templates (id) ON DELETE CASCADE
);

CREATE TABLE assess.task_templates (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	specialization_id BIGINT NOT NULL, 
	grade_id BIGINT NOT NULL, 
	skill_id BIGINT, 
	code VARCHAR(50) NOT NULL, 
	title VARCHAR(200) NOT NULL, 
	format assess.question_format NOT NULL, 
	difficulty SMALLINT NOT NULL, 
	expected_minutes SMALLINT, 
	max_score NUMERIC(6, 2) DEFAULT '1' NOT NULL, 
	generator_prompt TEXT, 
	grading_rubric TEXT, 
	params_schema JSONB, 
	source assess.template_source DEFAULT 'manual' NOT NULL, 
	version SMALLINT DEFAULT '1' NOT NULL, 
	is_active BOOLEAN DEFAULT 'true' NOT NULL, 
	usage_count INTEGER DEFAULT '0' NOT NULL, 
	avg_score NUMERIC(5, 2), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT task_templates_pkey PRIMARY KEY (id), 
	CONSTRAINT task_templates_difficulty_range CHECK (difficulty BETWEEN 1 AND 10), 
	CONSTRAINT task_templates_minutes_range CHECK (expected_minutes IS NULL OR expected_minutes BETWEEN 1 AND 240), 
	CONSTRAINT task_templates_max_score_positive CHECK (max_score > 0), 
	CONSTRAINT task_templates_gen_prompt_len CHECK (char_length(generator_prompt) <= 8000), 
	CONSTRAINT task_templates_rubric_len CHECK (char_length(grading_rubric) <= 8000), 
	CONSTRAINT task_templates_version_positive CHECK (version > 0), 
	CONSTRAINT task_templates_usage_non_negative CHECK (usage_count >= 0), 
	CONSTRAINT task_templates_avg_score_non_negative CHECK (avg_score IS NULL OR avg_score >= 0), 
	CONSTRAINT task_templates_specialization_id_fkey FOREIGN KEY(specialization_id) REFERENCES ref.specializations (id) ON DELETE CASCADE, 
	CONSTRAINT task_templates_grade_id_fkey FOREIGN KEY(grade_id) REFERENCES ref.grades (id) ON DELETE CASCADE, 
	CONSTRAINT task_templates_skill_id_fkey FOREIGN KEY(skill_id) REFERENCES ref.skills (id) ON DELETE SET NULL, 
	CONSTRAINT task_templates_code_key UNIQUE (code)
);

CREATE TABLE hire.employers (
	user_id UUID NOT NULL, 
	company_id UUID NOT NULL, 
	full_name VARCHAR(120) NOT NULL, 
	position VARCHAR(100), 
	contact_email CITEXT, 
	contact_phone VARCHAR(20), 
	is_owner BOOLEAN DEFAULT 'false' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT employers_pkey PRIMARY KEY (user_id), 
	CONSTRAINT employers_full_name_not_blank CHECK (char_length(btrim(full_name)) > 0), 
	CONSTRAINT employers_contact_email_format CHECK (contact_email IS NULL OR contact_email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[A-Za-z]{2,}$'), 
	CONSTRAINT employers_contact_phone_format CHECK (contact_phone IS NULL OR contact_phone ~ '^\+?[0-9]{7,15}$'), 
	CONSTRAINT employers_user_id_fkey FOREIGN KEY(user_id) REFERENCES core.users (id) ON DELETE CASCADE, 
	CONSTRAINT employers_company_id_fkey FOREIGN KEY(company_id) REFERENCES hire.companies (id) ON DELETE CASCADE
);

CREATE TABLE match.skill_relevance (
	specialization_id BIGINT NOT NULL, 
	skill_id BIGINT NOT NULL, 
	need_mentions INTEGER DEFAULT '0' NOT NULL, 
	need_total INTEGER DEFAULT '0' NOT NULL, 
	candidate_mentions INTEGER DEFAULT '0' NOT NULL, 
	weight NUMERIC(4, 3) NOT NULL, 
	computed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT skill_relevance_pkey PRIMARY KEY (specialization_id, skill_id), 
	CONSTRAINT skill_relevance_weight_range CHECK (weight BETWEEN 0 AND 1), 
	CONSTRAINT skill_relevance_need_mentions_non_negative CHECK (need_mentions >= 0), 
	CONSTRAINT skill_relevance_mentions_within_total CHECK (need_total >= need_mentions), 
	CONSTRAINT skill_relevance_candidate_mentions_non_negative CHECK (candidate_mentions >= 0), 
	CONSTRAINT skill_relevance_specialization_id_fkey FOREIGN KEY(specialization_id) REFERENCES ref.specializations (id) ON DELETE CASCADE, 
	CONSTRAINT skill_relevance_skill_id_fkey FOREIGN KEY(skill_id) REFERENCES ref.skills (id) ON DELETE CASCADE
);

CREATE TABLE ref.attribute_options (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	attribute_id BIGINT NOT NULL, 
	code VARCHAR(50) NOT NULL, 
	label VARCHAR(100) NOT NULL, 
	sort_order SMALLINT DEFAULT '0' NOT NULL, 
	CONSTRAINT attribute_options_pkey PRIMARY KEY (id), 
	CONSTRAINT attribute_options_attr_code_uq UNIQUE (attribute_id, code), 
	CONSTRAINT attribute_options_attr_id_uq UNIQUE (attribute_id, id), 
	CONSTRAINT attribute_options_code_format CHECK (code ~ '^[a-z][a-z0-9_]{1,49}$'), 
	CONSTRAINT attribute_options_label_not_blank CHECK (char_length(btrim(label)) > 0), 
	CONSTRAINT attribute_options_attribute_id_fkey FOREIGN KEY(attribute_id) REFERENCES ref.attribute_defs (id) ON DELETE CASCADE
);

CREATE TABLE ref.categories (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	specialization_id BIGINT NOT NULL, 
	grade_id BIGINT NOT NULL, 
	slug VARCHAR(90) NOT NULL, 
	is_active BOOLEAN DEFAULT 'true' NOT NULL, 
	CONSTRAINT categories_pkey PRIMARY KEY (id), 
	CONSTRAINT categories_spec_grade_uq UNIQUE (specialization_id, grade_id), 
	CONSTRAINT categories_slug_format CHECK (slug ~ '^[a-z][a-z0-9_.-]{1,89}$'), 
	CONSTRAINT categories_specialization_id_fkey FOREIGN KEY(specialization_id) REFERENCES ref.specializations (id) ON DELETE RESTRICT, 
	CONSTRAINT categories_grade_id_fkey FOREIGN KEY(grade_id) REFERENCES ref.grades (id) ON DELETE RESTRICT, 
	CONSTRAINT categories_slug_key UNIQUE (slug)
);

CREATE TABLE ref.skill_aliases (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	skill_id BIGINT NOT NULL, 
	alias CITEXT NOT NULL, 
	CONSTRAINT skill_aliases_pkey PRIMARY KEY (id), 
	CONSTRAINT skill_aliases_len CHECK (char_length(alias) BETWEEN 1 AND 60), 
	CONSTRAINT skill_aliases_skill_id_fkey FOREIGN KEY(skill_id) REFERENCES ref.skills (id) ON DELETE CASCADE, 
	CONSTRAINT skill_aliases_alias_key UNIQUE (alias)
);

CREATE TABLE ref.specialization_skills (
	specialization_id BIGINT NOT NULL, 
	skill_id BIGINT NOT NULL, 
	is_core BOOLEAN DEFAULT 'false' NOT NULL, 
	CONSTRAINT specialization_skills_pkey PRIMARY KEY (specialization_id, skill_id), 
	CONSTRAINT specialization_skills_specialization_id_fkey FOREIGN KEY(specialization_id) REFERENCES ref.specializations (id) ON DELETE CASCADE, 
	CONSTRAINT specialization_skills_skill_id_fkey FOREIGN KEY(skill_id) REFERENCES ref.skills (id) ON DELETE CASCADE
);

CREATE TABLE assess.survey_options (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	question_id BIGINT NOT NULL, 
	order_no SMALLINT NOT NULL, 
	label VARCHAR(200) NOT NULL, 
	specialization_id BIGINT, 
	skill_id BIGINT, 
	weight NUMERIC(4, 2) DEFAULT '1.00' NOT NULL, 
	CONSTRAINT survey_options_pkey PRIMARY KEY (id), 
	CONSTRAINT survey_options_order_uq UNIQUE (question_id, order_no), 
	CONSTRAINT survey_options_order_positive CHECK (order_no > 0), 
	CONSTRAINT survey_options_question_id_fkey FOREIGN KEY(question_id) REFERENCES assess.survey_questions (id) ON DELETE CASCADE, 
	CONSTRAINT survey_options_specialization_id_fkey FOREIGN KEY(specialization_id) REFERENCES ref.specializations (id) ON DELETE SET NULL, 
	CONSTRAINT survey_options_skill_id_fkey FOREIGN KEY(skill_id) REFERENCES ref.skills (id) ON DELETE SET NULL
);

CREATE TABLE hire.vacancies (
	id UUID DEFAULT uuidv7() NOT NULL, 
	company_id UUID NOT NULL, 
	author_user_id UUID, 
	title VARCHAR(120) NOT NULL, 
	description TEXT NOT NULL, 
	category_id BIGINT NOT NULL, 
	work_format profile.work_format, 
	employment_type profile.employment_type, 
	city VARCHAR(100), 
	salary_min INTEGER NOT NULL, 
	salary_max INTEGER NOT NULL, 
	currency VARCHAR(3) DEFAULT 'RUB' NOT NULL, 
	status hire.vacancy_status DEFAULT 'draft' NOT NULL, 
	published_at TIMESTAMP WITH TIME ZONE, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('russian', coalesce(title, '') || ' ' || coalesce(description, ''))) STORED, 
	CONSTRAINT vacancies_pkey PRIMARY KEY (id), 
	CONSTRAINT vacancies_title_not_blank CHECK (char_length(btrim(title)) > 0), 
	CONSTRAINT vacancies_description_len CHECK (char_length(description) BETWEEN 1 AND 20000), 
	CONSTRAINT vacancies_salary_min_range CHECK (salary_min > 0 AND salary_min <= 100000000), 
	CONSTRAINT vacancies_salary_max_range CHECK (salary_max > 0 AND salary_max <= 100000000), 
	CONSTRAINT vacancies_salary_range CHECK (salary_max >= salary_min), 
	CONSTRAINT vacancies_currency_format CHECK (currency ~ '^[A-Z]{3}$'), 
	CONSTRAINT vacancies_published_has_ts CHECK (status <> 'published' OR published_at IS NOT NULL), 
	CONSTRAINT vacancies_company_id_fkey FOREIGN KEY(company_id) REFERENCES hire.companies (id) ON DELETE CASCADE, 
	CONSTRAINT vacancies_author_user_id_fkey FOREIGN KEY(author_user_id) REFERENCES core.users (id) ON DELETE SET NULL, 
	CONSTRAINT vacancies_category_id_fkey FOREIGN KEY(category_id) REFERENCES ref.categories (id) ON DELETE RESTRICT
);

CREATE TABLE profile.candidates (
	user_id UUID NOT NULL, 
	first_name VARCHAR(50) NOT NULL, 
	last_name VARCHAR(50) NOT NULL, 
	middle_name VARCHAR(50), 
	birth_date DATE, 
	phone VARCHAR(20), 
	city VARCHAR(100), 
	relocation BOOLEAN DEFAULT 'false' NOT NULL, 
	work_format profile.work_format, 
	employment_type profile.employment_type, 
	experience_months SMALLINT DEFAULT '0' NOT NULL, 
	about TEXT, 
	current_category_id BIGINT, 
	is_open_to_offers BOOLEAN DEFAULT 'true' NOT NULL, 
	profile_strength NUMERIC(5, 2) DEFAULT '0' NOT NULL, 
	profile_refreshed_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('russian', coalesce(first_name, '') || ' ' || coalesce(last_name, '') || ' ' || coalesce(city, '') || ' ' || coalesce(about, ''))) STORED, 
	CONSTRAINT candidates_pkey PRIMARY KEY (user_id), 
	CONSTRAINT candidates_first_name_not_blank CHECK (char_length(btrim(first_name)) > 0), 
	CONSTRAINT candidates_last_name_not_blank CHECK (char_length(btrim(last_name)) > 0), 
	CONSTRAINT candidates_birth_date_sane CHECK (birth_date IS NULL OR birth_date BETWEEN DATE '1930-01-01' AND DATE '2020-01-01'), 
	CONSTRAINT candidates_phone_format CHECK (phone IS NULL OR phone ~ '^\+?[0-9]{7,15}$'), 
	CONSTRAINT candidates_experience_range CHECK (experience_months BETWEEN 0 AND 840), 
	CONSTRAINT candidates_about_len CHECK (char_length(about) <= 4000), 
	CONSTRAINT candidates_strength_range CHECK (profile_strength BETWEEN 0 AND 100), 
	CONSTRAINT candidates_user_id_fkey FOREIGN KEY(user_id) REFERENCES core.users (id) ON DELETE CASCADE, 
	CONSTRAINT candidates_current_category_id_fkey FOREIGN KEY(current_category_id) REFERENCES ref.categories (id) ON DELETE SET NULL
);

CREATE TABLE assess.survey_sessions (
	id UUID DEFAULT uuidv7() NOT NULL, 
	candidate_id UUID NOT NULL, 
	survey_id BIGINT NOT NULL, 
	status assess.session_status DEFAULT 'created' NOT NULL, 
	specialization_id BIGINT, 
	claimed_grade_id BIGINT, 
	started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT survey_sessions_pkey PRIMARY KEY (id), 
	CONSTRAINT survey_sessions_finish_after_start CHECK (finished_at IS NULL OR finished_at >= started_at), 
	CONSTRAINT survey_sessions_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES profile.candidates (user_id) ON DELETE CASCADE, 
	CONSTRAINT survey_sessions_survey_id_fkey FOREIGN KEY(survey_id) REFERENCES assess.survey_templates (id) ON DELETE RESTRICT, 
	CONSTRAINT survey_sessions_specialization_id_fkey FOREIGN KEY(specialization_id) REFERENCES ref.specializations (id) ON DELETE SET NULL, 
	CONSTRAINT survey_sessions_claimed_grade_id_fkey FOREIGN KEY(claimed_grade_id) REFERENCES ref.grades (id) ON DELETE SET NULL
);

CREATE TABLE assess.tasks (
	id UUID DEFAULT uuidv7() NOT NULL, 
	template_id BIGINT NOT NULL, 
	candidate_id UUID, 
	statement TEXT NOT NULL, 
	payload JSONB, 
	answer_key JSONB, 
	difficulty SMALLINT NOT NULL, 
	max_score NUMERIC(6, 2) NOT NULL, 
	generated_by VARCHAR(60), 
	generation_seed BIGINT, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT tasks_pkey PRIMARY KEY (id), 
	CONSTRAINT tasks_statement_len CHECK (char_length(statement) BETWEEN 1 AND 20000), 
	CONSTRAINT tasks_difficulty_range CHECK (difficulty BETWEEN 1 AND 10), 
	CONSTRAINT tasks_max_score_positive CHECK (max_score > 0), 
	CONSTRAINT tasks_template_id_fkey FOREIGN KEY(template_id) REFERENCES assess.task_templates (id) ON DELETE RESTRICT, 
	CONSTRAINT tasks_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES profile.candidates (user_id) ON DELETE CASCADE
);

CREATE TABLE hire.applications (
	id UUID DEFAULT uuidv7() NOT NULL, 
	vacancy_id UUID NOT NULL, 
	candidate_id UUID NOT NULL, 
	cover_letter TEXT, 
	status hire.application_status DEFAULT 'sent' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	viewed_at TIMESTAMP WITH TIME ZONE, 
	responded_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT applications_pkey PRIMARY KEY (id), 
	CONSTRAINT applications_unique UNIQUE (vacancy_id, candidate_id), 
	CONSTRAINT applications_cover_letter_len CHECK (char_length(cover_letter) <= 5000), 
	CONSTRAINT applications_vacancy_id_fkey FOREIGN KEY(vacancy_id) REFERENCES hire.vacancies (id) ON DELETE CASCADE, 
	CONSTRAINT applications_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES profile.candidates (user_id) ON DELETE CASCADE
);

CREATE TABLE hire.employer_needs (
	id UUID DEFAULT uuidv7() NOT NULL, 
	company_id UUID NOT NULL, 
	author_user_id UUID, 
	vacancy_id UUID, 
	title VARCHAR(120) NOT NULL, 
	description TEXT NOT NULL, 
	specialization_id BIGINT, 
	grade_id BIGINT, 
	category_id BIGINT, 
	positive_prompt VARCHAR(2000), 
	negative_prompt VARCHAR(2000), 
	salary_min INTEGER, 
	salary_max INTEGER, 
	currency VARCHAR(3) DEFAULT 'RUB' NOT NULL, 
	is_active BOOLEAN DEFAULT 'true' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT employer_needs_pkey PRIMARY KEY (id), 
	CONSTRAINT employer_needs_description_len CHECK (char_length(description) BETWEEN 1 AND 20000), 
	CONSTRAINT employer_needs_salary_min_positive CHECK (salary_min IS NULL OR salary_min > 0), 
	CONSTRAINT employer_needs_salary_max_positive CHECK (salary_max IS NULL OR salary_max > 0), 
	CONSTRAINT employer_needs_salary_range CHECK (salary_max IS NULL OR salary_min IS NULL OR salary_max >= salary_min), 
	CONSTRAINT employer_needs_currency_format CHECK (currency ~ '^[A-Z]{3}$'), 
	CONSTRAINT employer_needs_company_id_fkey FOREIGN KEY(company_id) REFERENCES hire.companies (id) ON DELETE CASCADE, 
	CONSTRAINT employer_needs_author_user_id_fkey FOREIGN KEY(author_user_id) REFERENCES core.users (id) ON DELETE SET NULL, 
	CONSTRAINT employer_needs_vacancy_id_fkey FOREIGN KEY(vacancy_id) REFERENCES hire.vacancies (id) ON DELETE SET NULL, 
	CONSTRAINT employer_needs_specialization_id_fkey FOREIGN KEY(specialization_id) REFERENCES ref.specializations (id) ON DELETE SET NULL, 
	CONSTRAINT employer_needs_grade_id_fkey FOREIGN KEY(grade_id) REFERENCES ref.grades (id) ON DELETE SET NULL, 
	CONSTRAINT employer_needs_category_id_fkey FOREIGN KEY(category_id) REFERENCES ref.categories (id) ON DELETE SET NULL
);

CREATE TABLE hire.vacancy_skills (
	vacancy_id UUID NOT NULL, 
	skill_id BIGINT NOT NULL, 
	importance hire.skill_importance DEFAULT 'required' NOT NULL, 
	min_level INTEGER, 
	CONSTRAINT vacancy_skills_pkey PRIMARY KEY (vacancy_id, skill_id), 
	CONSTRAINT vacancy_skills_min_level_range CHECK (min_level IS NULL OR min_level BETWEEN 0 AND 5), 
	CONSTRAINT vacancy_skills_vacancy_id_fkey FOREIGN KEY(vacancy_id) REFERENCES hire.vacancies (id) ON DELETE CASCADE, 
	CONSTRAINT vacancy_skills_skill_id_fkey FOREIGN KEY(skill_id) REFERENCES ref.skills (id) ON DELETE RESTRICT
);

CREATE TABLE match.candidate_embeddings (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	candidate_id UUID NOT NULL, 
	model VARCHAR(60) NOT NULL, 
	embedding VECTOR(1024) NOT NULL, 
	source_hash VARCHAR(64) NOT NULL, 
	built_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT candidate_embeddings_pkey PRIMARY KEY (id), 
	CONSTRAINT candidate_embeddings_uq UNIQUE (candidate_id, model), 
	CONSTRAINT candidate_embeddings_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES profile.candidates (user_id) ON DELETE CASCADE
);

CREATE TABLE match.vacancy_embeddings (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	vacancy_id UUID NOT NULL, 
	model VARCHAR(60) NOT NULL, 
	embedding VECTOR(1024) NOT NULL, 
	source_hash VARCHAR(64) NOT NULL, 
	built_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT vacancy_embeddings_pkey PRIMARY KEY (id), 
	CONSTRAINT vacancy_embeddings_uq UNIQUE (vacancy_id, model), 
	CONSTRAINT vacancy_embeddings_vacancy_id_fkey FOREIGN KEY(vacancy_id) REFERENCES hire.vacancies (id) ON DELETE CASCADE
);

CREATE TABLE profile.candidate_attribute_values (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	candidate_id UUID NOT NULL, 
	attribute_id BIGINT NOT NULL, 
	data_type ref.attribute_data_type NOT NULL, 
	value_string VARCHAR(255), 
	value_text TEXT, 
	value_integer INTEGER, 
	value_decimal NUMERIC(14, 4), 
	value_boolean BOOLEAN, 
	value_date DATE, 
	option_id BIGINT, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT candidate_attribute_values_pkey PRIMARY KEY (id), 
	CONSTRAINT cav_attribute_fk FOREIGN KEY(attribute_id, data_type) REFERENCES ref.attribute_defs (id, data_type) ON DELETE CASCADE, 
	CONSTRAINT cav_option_fk FOREIGN KEY(attribute_id, option_id) REFERENCES ref.attribute_options (attribute_id, id) ON DELETE RESTRICT, 
	CONSTRAINT candidate_attribute_values_cav_text_len CHECK (char_length(value_text) <= 4000), 
	CONSTRAINT candidate_attribute_values_cav_value_matches_type CHECK ((data_type = 'string'  AND value_string  IS NOT NULL) OR (data_type = 'text'    AND value_text    IS NOT NULL) OR (data_type = 'integer' AND value_integer IS NOT NULL) OR (data_type = 'decimal' AND value_decimal IS NOT NULL) OR (data_type = 'boolean' AND value_boolean IS NOT NULL) OR (data_type = 'date'    AND value_date    IS NOT NULL) OR (data_type = 'option'  AND option_id     IS NOT NULL)), 
	CONSTRAINT candidate_attribute_values_cav_exactly_one_value CHECK ((value_string  IS NOT NULL)::int + (value_text    IS NOT NULL)::int + (value_integer IS NOT NULL)::int + (value_decimal IS NOT NULL)::int + (value_boolean IS NOT NULL)::int + (value_date    IS NOT NULL)::int + (option_id     IS NOT NULL)::int = 1), 
	CONSTRAINT cav_unique_value UNIQUE NULLS NOT DISTINCT (candidate_id, attribute_id, option_id), 
	CONSTRAINT candidate_attribute_values_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES profile.candidates (user_id) ON DELETE CASCADE
);

CREATE TABLE profile.candidate_links (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	candidate_id UUID NOT NULL, 
	kind profile.link_kind NOT NULL, 
	url VARCHAR(2048) NOT NULL, 
	label VARCHAR(60), 
	is_public BOOLEAN DEFAULT 'true' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT candidate_links_pkey PRIMARY KEY (id), 
	CONSTRAINT candidate_links_unique UNIQUE (candidate_id, kind, url), 
	CONSTRAINT candidate_links_url_format CHECK (url ~ '^https?://[^[:space:]]{3,}$'), 
	CONSTRAINT candidate_links_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES profile.candidates (user_id) ON DELETE CASCADE
);

CREATE TABLE profile.candidate_preferences (
	candidate_id UUID NOT NULL, 
	desired_specialization_id BIGINT, 
	salary_min INTEGER, 
	salary_max INTEGER, 
	currency VARCHAR(3) DEFAULT 'RUB' NOT NULL, 
	positive_prompt VARCHAR(1000), 
	negative_prompt VARCHAR(1000), 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT candidate_preferences_pkey PRIMARY KEY (candidate_id), 
	CONSTRAINT candidate_preferences_salary_min_range CHECK (salary_min IS NULL OR (salary_min > 0 AND salary_min <= 100000000)), 
	CONSTRAINT candidate_preferences_salary_max_range CHECK (salary_max IS NULL OR (salary_max > 0 AND salary_max <= 100000000)), 
	CONSTRAINT candidate_preferences_salary_range CHECK (salary_max IS NULL OR salary_min IS NULL OR salary_max >= salary_min), 
	CONSTRAINT candidate_preferences_currency_format CHECK (currency ~ '^[A-Z]{3}$'), 
	CONSTRAINT candidate_preferences_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES profile.candidates (user_id) ON DELETE CASCADE, 
	CONSTRAINT candidate_preferences_desired_specialization_id_fkey FOREIGN KEY(desired_specialization_id) REFERENCES ref.specializations (id) ON DELETE SET NULL
);

CREATE TABLE profile.candidate_privacy (
	candidate_id UUID NOT NULL, 
	is_searchable BOOLEAN DEFAULT 'true' NOT NULL, 
	show_contacts BOOLEAN DEFAULT 'false' NOT NULL, 
	show_salary BOOLEAN DEFAULT 'true' NOT NULL, 
	show_fsp BOOLEAN DEFAULT 'true' NOT NULL, 
	show_github BOOLEAN DEFAULT 'true' NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT candidate_privacy_pkey PRIMARY KEY (candidate_id), 
	CONSTRAINT candidate_privacy_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES profile.candidates (user_id) ON DELETE CASCADE
);

CREATE TABLE profile.candidate_skills (
	candidate_id UUID NOT NULL, 
	skill_id BIGINT NOT NULL, 
	self_level SMALLINT DEFAULT '0' NOT NULL, 
	confirmed_level SMALLINT, 
	years NUMERIC(3, 1), 
	source profile.skill_source DEFAULT 'self' NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT candidate_skills_pkey PRIMARY KEY (candidate_id, skill_id), 
	CONSTRAINT candidate_skills_self_level_range CHECK (self_level BETWEEN 0 AND 5), 
	CONSTRAINT candidate_skills_confirmed_level_range CHECK (confirmed_level IS NULL OR confirmed_level BETWEEN 0 AND 5), 
	CONSTRAINT candidate_skills_years_range CHECK (years IS NULL OR (years >= 0 AND years <= 70)), 
	CONSTRAINT candidate_skills_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES profile.candidates (user_id) ON DELETE CASCADE, 
	CONSTRAINT candidate_skills_skill_id_fkey FOREIGN KEY(skill_id) REFERENCES ref.skills (id) ON DELETE RESTRICT
);

CREATE TABLE profile.fsp_achievements (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	candidate_id UUID NOT NULL, 
	event_id BIGINT NOT NULL, 
	place SMALLINT, 
	participants INTEGER, 
	team_name VARCHAR(100), 
	score NUMERIC(10, 3), 
	source profile.achievement_source DEFAULT 'registry' NOT NULL, 
	confirmed_at TIMESTAMP WITH TIME ZONE, 
	raw JSONB, 
	CONSTRAINT fsp_achievements_pkey PRIMARY KEY (id), 
	CONSTRAINT fsp_achievements_unique UNIQUE (candidate_id, event_id), 
	CONSTRAINT fsp_achievements_place_range CHECK (place IS NULL OR (place > 0 AND place <= 10000)), 
	CONSTRAINT fsp_achievements_participants_positive CHECK (participants IS NULL OR participants > 0), 
	CONSTRAINT fsp_achievements_place_within_participants CHECK (participants IS NULL OR place IS NULL OR place <= participants), 
	CONSTRAINT fsp_achievements_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES profile.candidates (user_id) ON DELETE CASCADE, 
	CONSTRAINT fsp_achievements_event_id_fkey FOREIGN KEY(event_id) REFERENCES profile.fsp_events (id) ON DELETE RESTRICT
);

CREATE TABLE profile.fsp_profiles (
	candidate_id UUID NOT NULL, 
	fsp_participant_id VARCHAR(64), 
	verification_status profile.fsp_link_status DEFAULT 'unlinked' NOT NULL, 
	raw_payload JSONB, 
	linked_at TIMESTAMP WITH TIME ZONE, 
	synced_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT fsp_profiles_pkey PRIMARY KEY (candidate_id), 
	CONSTRAINT fsp_profiles_verified_has_id CHECK (verification_status <> 'verified' OR fsp_participant_id IS NOT NULL), 
	CONSTRAINT fsp_profiles_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES profile.candidates (user_id) ON DELETE CASCADE, 
	CONSTRAINT fsp_profiles_fsp_participant_id_key UNIQUE (fsp_participant_id)
);

CREATE TABLE profile.github_accounts (
	candidate_id UUID NOT NULL, 
	github_user_id BIGINT NOT NULL, 
	login VARCHAR(39) NOT NULL, 
	installation_id BIGINT, 
	access_token_enc BYTEA, 
	scopes VARCHAR(255), 
	linked_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	indexed_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT github_accounts_pkey PRIMARY KEY (candidate_id), 
	CONSTRAINT github_accounts_login_format CHECK (login ~ '^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$'), 
	CONSTRAINT github_accounts_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES profile.candidates (user_id) ON DELETE CASCADE, 
	CONSTRAINT github_accounts_github_user_id_key UNIQUE (github_user_id), 
	CONSTRAINT github_accounts_login_key UNIQUE (login)
);

CREATE TABLE profile.github_signals (
	candidate_id UUID NOT NULL, 
	skill_id BIGINT NOT NULL, 
	score NUMERIC(5, 2) NOT NULL, 
	evidence JSONB, 
	computed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT github_signals_pkey PRIMARY KEY (candidate_id, skill_id), 
	CONSTRAINT github_signals_score_range CHECK (score BETWEEN 0 AND 100), 
	CONSTRAINT github_signals_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES profile.candidates (user_id) ON DELETE CASCADE, 
	CONSTRAINT github_signals_skill_id_fkey FOREIGN KEY(skill_id) REFERENCES ref.skills (id) ON DELETE CASCADE
);

CREATE TABLE profile.resume_documents (
	id UUID DEFAULT uuidv7() NOT NULL, 
	candidate_id UUID NOT NULL, 
	kind profile.resume_kind NOT NULL, 
	storage_key VARCHAR(512) NOT NULL, 
	file_name VARCHAR(255), 
	mime_type VARCHAR(100) DEFAULT 'application/pdf' NOT NULL, 
	size_bytes INTEGER, 
	parse_status profile.parse_status DEFAULT 'pending' NOT NULL, 
	raw_text TEXT, 
	uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	parsed_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT resume_documents_pkey PRIMARY KEY (id), 
	CONSTRAINT resume_documents_key_not_blank CHECK (char_length(btrim(storage_key)) > 0), 
	CONSTRAINT resume_documents_size_range CHECK (size_bytes IS NULL OR (size_bytes > 0 AND size_bytes <= 20971520)), 
	CONSTRAINT resume_documents_raw_text_len CHECK (char_length(raw_text) <= 200000), 
	CONSTRAINT resume_documents_generated_is_done CHECK (kind <> 'generated' OR parse_status = 'done'), 
	CONSTRAINT resume_documents_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES profile.candidates (user_id) ON DELETE CASCADE
);

CREATE TABLE assess.regular_assignments (
	id UUID DEFAULT uuidv7() NOT NULL, 
	candidate_id UUID NOT NULL, 
	task_id UUID NOT NULL, 
	company_id UUID, 
	status assess.assignment_status DEFAULT 'issued' NOT NULL, 
	answer_text TEXT, 
	score NUMERIC(6, 2), 
	max_score NUMERIC(6, 2), 
	issued_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	due_at TIMESTAMP WITH TIME ZONE, 
	answered_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT regular_assignments_pkey PRIMARY KEY (id), 
	CONSTRAINT regular_assignments_answer_len CHECK (char_length(answer_text) <= 20000), 
	CONSTRAINT regular_assignments_score_non_negative CHECK (score IS NULL OR score >= 0), 
	CONSTRAINT regular_assignments_max_score_positive CHECK (max_score IS NULL OR max_score > 0), 
	CONSTRAINT regular_assignments_score_within_max CHECK (score IS NULL OR max_score IS NULL OR score <= max_score), 
	CONSTRAINT regular_assignments_due_after_issue CHECK (due_at IS NULL OR due_at > issued_at), 
	CONSTRAINT regular_assignments_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES profile.candidates (user_id) ON DELETE CASCADE, 
	CONSTRAINT regular_assignments_task_id_fkey FOREIGN KEY(task_id) REFERENCES assess.tasks (id) ON DELETE RESTRICT, 
	CONSTRAINT regular_assignments_company_id_fkey FOREIGN KEY(company_id) REFERENCES hire.companies (id) ON DELETE SET NULL
);

CREATE TABLE assess.survey_answers (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	session_id UUID NOT NULL, 
	question_id BIGINT NOT NULL, 
	option_id BIGINT, 
	free_text VARCHAR(1000), 
	answered_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT survey_answers_pkey PRIMARY KEY (id), 
	CONSTRAINT survey_answers_unique UNIQUE NULLS NOT DISTINCT (session_id, question_id, option_id), 
	CONSTRAINT survey_answers_has_value CHECK (option_id IS NOT NULL OR free_text IS NOT NULL), 
	CONSTRAINT survey_answers_session_id_fkey FOREIGN KEY(session_id) REFERENCES assess.survey_sessions (id) ON DELETE CASCADE, 
	CONSTRAINT survey_answers_question_id_fkey FOREIGN KEY(question_id) REFERENCES assess.survey_questions (id) ON DELETE CASCADE, 
	CONSTRAINT survey_answers_option_id_fkey FOREIGN KEY(option_id) REFERENCES assess.survey_options (id) ON DELETE CASCADE
);

CREATE TABLE assess.test_sessions (
	id UUID DEFAULT uuidv7() NOT NULL, 
	candidate_id UUID NOT NULL, 
	survey_session_id UUID, 
	specialization_id BIGINT NOT NULL, 
	claimed_grade_id BIGINT NOT NULL, 
	status assess.session_status DEFAULT 'created' NOT NULL, 
	outcome assess.session_outcome, 
	assigned_category_id BIGINT, 
	total_score NUMERIC(7, 2), 
	max_score NUMERIC(7, 2), 
	started_at TIMESTAMP WITH TIME ZONE, 
	deadline_at TIMESTAMP WITH TIME ZONE, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT test_sessions_pkey PRIMARY KEY (id), 
	CONSTRAINT test_sessions_score_within_max CHECK (total_score IS NULL OR max_score IS NULL OR total_score <= max_score), 
	CONSTRAINT test_sessions_finish_after_start CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at), 
	CONSTRAINT test_sessions_scored_has_result CHECK (status <> 'scored' OR (outcome IS NOT NULL AND total_score IS NOT NULL)), 
	CONSTRAINT test_sessions_total_score_non_negative CHECK (total_score IS NULL OR total_score >= 0), 
	CONSTRAINT test_sessions_max_score_positive CHECK (max_score IS NULL OR max_score > 0), 
	CONSTRAINT test_sessions_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES profile.candidates (user_id) ON DELETE CASCADE, 
	CONSTRAINT test_sessions_survey_session_id_fkey FOREIGN KEY(survey_session_id) REFERENCES assess.survey_sessions (id) ON DELETE SET NULL, 
	CONSTRAINT test_sessions_specialization_id_fkey FOREIGN KEY(specialization_id) REFERENCES ref.specializations (id) ON DELETE RESTRICT, 
	CONSTRAINT test_sessions_claimed_grade_id_fkey FOREIGN KEY(claimed_grade_id) REFERENCES ref.grades (id) ON DELETE RESTRICT, 
	CONSTRAINT test_sessions_assigned_category_id_fkey FOREIGN KEY(assigned_category_id) REFERENCES ref.categories (id) ON DELETE SET NULL
);

CREATE TABLE hire.application_events (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	application_id UUID NOT NULL, 
	from_status hire.application_status, 
	to_status hire.application_status NOT NULL, 
	actor_user_id UUID, 
	comment VARCHAR(300), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT application_events_pkey PRIMARY KEY (id), 
	CONSTRAINT application_events_status_changed CHECK (from_status IS DISTINCT FROM to_status), 
	CONSTRAINT application_events_application_id_fkey FOREIGN KEY(application_id) REFERENCES hire.applications (id) ON DELETE CASCADE, 
	CONSTRAINT application_events_actor_user_id_fkey FOREIGN KEY(actor_user_id) REFERENCES core.users (id) ON DELETE SET NULL
);

CREATE TABLE hire.invitations (
	id UUID DEFAULT uuidv7() NOT NULL, 
	company_id UUID NOT NULL, 
	sender_user_id UUID, 
	candidate_id UUID NOT NULL, 
	vacancy_id UUID, 
	need_id UUID, 
	message TEXT NOT NULL, 
	salary_min INTEGER NOT NULL, 
	salary_max INTEGER NOT NULL, 
	currency VARCHAR(3) DEFAULT 'RUB' NOT NULL, 
	contact_method hire.contact_method DEFAULT 'platform' NOT NULL, 
	contact_value VARCHAR(120), 
	status hire.invitation_status DEFAULT 'sent' NOT NULL, 
	decline_reason VARCHAR(300), 
	sent_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	viewed_at TIMESTAMP WITH TIME ZONE, 
	responded_at TIMESTAMP WITH TIME ZONE, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT invitations_pkey PRIMARY KEY (id), 
	CONSTRAINT invitations_message_len CHECK (char_length(message) BETWEEN 1 AND 5000), 
	CONSTRAINT invitations_salary_min_range CHECK (salary_min > 0 AND salary_min <= 100000000), 
	CONSTRAINT invitations_salary_max_range CHECK (salary_max > 0 AND salary_max <= 100000000), 
	CONSTRAINT invitations_salary_range CHECK (salary_max >= salary_min), 
	CONSTRAINT invitations_currency_format CHECK (currency ~ '^[A-Z]{3}$'), 
	CONSTRAINT invitations_viewed_after_sent CHECK (viewed_at IS NULL OR viewed_at >= sent_at), 
	CONSTRAINT invitations_responded_after_sent CHECK (responded_at IS NULL OR responded_at >= sent_at), 
	CONSTRAINT invitations_final_has_response CHECK (status NOT IN ('accepted', 'declined') OR responded_at IS NOT NULL), 
	CONSTRAINT invitations_contact_value_present CHECK (contact_method = 'platform' OR char_length(btrim(coalesce(contact_value, ''))) > 0), 
	CONSTRAINT invitations_company_id_fkey FOREIGN KEY(company_id) REFERENCES hire.companies (id) ON DELETE CASCADE, 
	CONSTRAINT invitations_sender_user_id_fkey FOREIGN KEY(sender_user_id) REFERENCES core.users (id) ON DELETE SET NULL, 
	CONSTRAINT invitations_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES profile.candidates (user_id) ON DELETE CASCADE, 
	CONSTRAINT invitations_vacancy_id_fkey FOREIGN KEY(vacancy_id) REFERENCES hire.vacancies (id) ON DELETE SET NULL, 
	CONSTRAINT invitations_need_id_fkey FOREIGN KEY(need_id) REFERENCES hire.employer_needs (id) ON DELETE SET NULL
);

CREATE TABLE hire.need_skills (
	need_id UUID NOT NULL, 
	skill_id BIGINT NOT NULL, 
	importance hire.skill_importance DEFAULT 'required' NOT NULL, 
	min_level INTEGER, 
	CONSTRAINT need_skills_pkey PRIMARY KEY (need_id, skill_id), 
	CONSTRAINT need_skills_min_level_range CHECK (min_level IS NULL OR min_level BETWEEN 0 AND 5), 
	CONSTRAINT need_skills_need_id_fkey FOREIGN KEY(need_id) REFERENCES hire.employer_needs (id) ON DELETE CASCADE, 
	CONSTRAINT need_skills_skill_id_fkey FOREIGN KEY(skill_id) REFERENCES ref.skills (id) ON DELETE RESTRICT
);

CREATE TABLE match.chat_threads (
	id UUID DEFAULT uuidv7() NOT NULL, 
	need_id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	title VARCHAR(120), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT chat_threads_pkey PRIMARY KEY (id), 
	CONSTRAINT chat_threads_need_id_fkey FOREIGN KEY(need_id) REFERENCES hire.employer_needs (id) ON DELETE CASCADE, 
	CONSTRAINT chat_threads_user_id_fkey FOREIGN KEY(user_id) REFERENCES core.users (id) ON DELETE CASCADE
);

CREATE TABLE match.need_embeddings (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	need_id UUID NOT NULL, 
	model VARCHAR(60) NOT NULL, 
	embedding VECTOR(1024) NOT NULL, 
	source_hash VARCHAR(64) NOT NULL, 
	built_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT need_embeddings_pkey PRIMARY KEY (id), 
	CONSTRAINT need_embeddings_uq UNIQUE (need_id, model), 
	CONSTRAINT need_embeddings_need_id_fkey FOREIGN KEY(need_id) REFERENCES hire.employer_needs (id) ON DELETE CASCADE
);

CREATE TABLE match.search_runs (
	id UUID DEFAULT uuidv7() NOT NULL, 
	need_id UUID NOT NULL, 
	author_user_id UUID, 
	parent_run_id UUID, 
	strategy VARCHAR(40) DEFAULT 'prefilter_vector_llm' NOT NULL, 
	params JSONB, 
	status match.run_status DEFAULT 'queued' NOT NULL, 
	pool_size INTEGER, 
	llm_model VARCHAR(60), 
	summary TEXT, 
	error_message VARCHAR(500), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	finished_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT search_runs_pkey PRIMARY KEY (id), 
	CONSTRAINT search_runs_no_self_parent CHECK (parent_run_id IS DISTINCT FROM id), 
	CONSTRAINT search_runs_failed_has_error CHECK (status <> 'failed' OR error_message IS NOT NULL), 
	CONSTRAINT search_runs_pool_non_negative CHECK (pool_size IS NULL OR pool_size >= 0), 
	CONSTRAINT search_runs_summary_len CHECK (char_length(summary) <= 10000), 
	CONSTRAINT search_runs_need_id_fkey FOREIGN KEY(need_id) REFERENCES hire.employer_needs (id) ON DELETE CASCADE, 
	CONSTRAINT search_runs_author_user_id_fkey FOREIGN KEY(author_user_id) REFERENCES core.users (id) ON DELETE SET NULL, 
	CONSTRAINT search_runs_parent_run_id_fkey FOREIGN KEY(parent_run_id) REFERENCES match.search_runs (id) ON DELETE SET NULL
);

CREATE TABLE profile.github_repos (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	candidate_id UUID NOT NULL, 
	github_repo_id BIGINT NOT NULL, 
	full_name VARCHAR(140) NOT NULL, 
	description VARCHAR(500), 
	is_private BOOLEAN DEFAULT 'false' NOT NULL, 
	is_fork BOOLEAN DEFAULT 'false' NOT NULL, 
	stars INTEGER DEFAULT '0' NOT NULL, 
	forks INTEGER DEFAULT '0' NOT NULL, 
	commits_by_owner INTEGER, 
	primary_language VARCHAR(40), 
	pushed_at TIMESTAMP WITH TIME ZONE, 
	indexed_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT github_repos_pkey PRIMARY KEY (id), 
	CONSTRAINT github_repos_stars_non_negative CHECK (stars >= 0), 
	CONSTRAINT github_repos_forks_non_negative CHECK (forks >= 0), 
	CONSTRAINT github_repos_commits_non_negative CHECK (commits_by_owner IS NULL OR commits_by_owner >= 0), 
	CONSTRAINT github_repos_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES profile.github_accounts (candidate_id) ON DELETE CASCADE, 
	CONSTRAINT github_repos_github_repo_id_key UNIQUE (github_repo_id)
);

CREATE TABLE profile.resume_parse_results (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	document_id UUID NOT NULL, 
	model VARCHAR(60) NOT NULL, 
	payload JSONB NOT NULL, 
	confidence NUMERIC(3, 2), 
	applied_at TIMESTAMP WITH TIME ZONE, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT resume_parse_results_pkey PRIMARY KEY (id), 
	CONSTRAINT resume_parse_results_confidence_range CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1), 
	CONSTRAINT resume_parse_results_document_id_fkey FOREIGN KEY(document_id) REFERENCES profile.resume_documents (id) ON DELETE CASCADE
);

CREATE TABLE assess.grade_assignments (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	candidate_id UUID NOT NULL, 
	category_id BIGINT NOT NULL, 
	source assess.grade_source DEFAULT 'test' NOT NULL, 
	test_session_id UUID, 
	assigned_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	next_change_allowed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	is_current BOOLEAN DEFAULT 'true' NOT NULL, 
	note VARCHAR(300), 
	CONSTRAINT grade_assignments_pkey PRIMARY KEY (id), 
	CONSTRAINT grade_assignments_cooldown_in_future CHECK (next_change_allowed_at >= assigned_at), 
	CONSTRAINT grade_assignments_test_source_has_session CHECK (source <> 'test' OR test_session_id IS NOT NULL), 
	CONSTRAINT grade_assignments_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES profile.candidates (user_id) ON DELETE CASCADE, 
	CONSTRAINT grade_assignments_category_id_fkey FOREIGN KEY(category_id) REFERENCES ref.categories (id) ON DELETE RESTRICT, 
	CONSTRAINT grade_assignments_test_session_id_fkey FOREIGN KEY(test_session_id) REFERENCES assess.test_sessions (id) ON DELETE SET NULL
);

CREATE TABLE assess.task_answers (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	session_id UUID NOT NULL, 
	task_id UUID NOT NULL, 
	answer_text TEXT, 
	answer_payload JSONB, 
	score NUMERIC(6, 2), 
	max_score NUMERIC(6, 2), 
	checked_by assess.checked_by, 
	feedback VARCHAR(2000), 
	submitted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	checked_at TIMESTAMP WITH TIME ZONE, 
	time_spent_sec INTEGER, 
	paste_count SMALLINT, 
	focus_lost_count SMALLINT, 
	CONSTRAINT task_answers_pkey PRIMARY KEY (id), 
	CONSTRAINT task_answers_unique UNIQUE (session_id, task_id), 
	CONSTRAINT task_answers_text_len CHECK (char_length(answer_text) <= 50000), 
	CONSTRAINT task_answers_score_non_negative CHECK (score IS NULL OR score >= 0), 
	CONSTRAINT task_answers_max_score_positive CHECK (max_score IS NULL OR max_score > 0), 
	CONSTRAINT task_answers_score_within_max CHECK (score IS NULL OR max_score IS NULL OR score <= max_score), 
	CONSTRAINT task_answers_time_non_negative CHECK (time_spent_sec IS NULL OR time_spent_sec >= 0), 
	CONSTRAINT task_answers_paste_non_negative CHECK (paste_count IS NULL OR paste_count >= 0), 
	CONSTRAINT task_answers_focus_non_negative CHECK (focus_lost_count IS NULL OR focus_lost_count >= 0), 
	CONSTRAINT task_answers_session_id_fkey FOREIGN KEY(session_id) REFERENCES assess.test_sessions (id) ON DELETE CASCADE, 
	CONSTRAINT task_answers_task_id_fkey FOREIGN KEY(task_id) REFERENCES assess.tasks (id) ON DELETE CASCADE
);

CREATE TABLE assess.test_session_tasks (
	session_id UUID NOT NULL, 
	task_id UUID NOT NULL, 
	order_no SMALLINT NOT NULL, 
	weight NUMERIC(4, 2) DEFAULT '1.00' NOT NULL, 
	CONSTRAINT test_session_tasks_pkey PRIMARY KEY (session_id, task_id), 
	CONSTRAINT test_session_tasks_order_uq UNIQUE (session_id, order_no), 
	CONSTRAINT test_session_tasks_task_uq UNIQUE (task_id), 
	CONSTRAINT test_session_tasks_order_positive CHECK (order_no > 0), 
	CONSTRAINT test_session_tasks_weight_positive CHECK (weight > 0), 
	CONSTRAINT test_session_tasks_session_id_fkey FOREIGN KEY(session_id) REFERENCES assess.test_sessions (id) ON DELETE CASCADE, 
	CONSTRAINT test_session_tasks_task_id_fkey FOREIGN KEY(task_id) REFERENCES assess.tasks (id) ON DELETE RESTRICT
);

CREATE TABLE hire.contact_disclosures (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	candidate_id UUID NOT NULL, 
	company_id UUID NOT NULL, 
	reason hire.disclosure_reason NOT NULL, 
	invitation_id UUID, 
	application_id UUID, 
	granted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	CONSTRAINT contact_disclosures_pkey PRIMARY KEY (id), 
	CONSTRAINT contact_disclosures_source_matches_reason CHECK ((reason = 'invitation_accepted' AND invitation_id IS NOT NULL AND application_id IS NULL) OR (reason = 'application_sent' AND application_id IS NOT NULL AND invitation_id IS NULL)), 
	CONSTRAINT contact_disclosures_revoked_after_granted CHECK (revoked_at IS NULL OR revoked_at >= granted_at), 
	CONSTRAINT contact_disclosures_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES profile.candidates (user_id) ON DELETE CASCADE, 
	CONSTRAINT contact_disclosures_company_id_fkey FOREIGN KEY(company_id) REFERENCES hire.companies (id) ON DELETE CASCADE, 
	CONSTRAINT contact_disclosures_invitation_id_fkey FOREIGN KEY(invitation_id) REFERENCES hire.invitations (id) ON DELETE CASCADE, 
	CONSTRAINT contact_disclosures_application_id_fkey FOREIGN KEY(application_id) REFERENCES hire.applications (id) ON DELETE CASCADE
);

CREATE TABLE hire.invitation_events (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	invitation_id UUID NOT NULL, 
	from_status hire.invitation_status, 
	to_status hire.invitation_status NOT NULL, 
	actor_user_id UUID, 
	comment VARCHAR(300), 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT invitation_events_pkey PRIMARY KEY (id), 
	CONSTRAINT invitation_events_status_changed CHECK (from_status IS DISTINCT FROM to_status), 
	CONSTRAINT invitation_events_invitation_id_fkey FOREIGN KEY(invitation_id) REFERENCES hire.invitations (id) ON DELETE CASCADE, 
	CONSTRAINT invitation_events_actor_user_id_fkey FOREIGN KEY(actor_user_id) REFERENCES core.users (id) ON DELETE SET NULL
);

CREATE TABLE match.chat_messages (
	id BIGINT GENERATED ALWAYS AS IDENTITY, 
	thread_id UUID NOT NULL, 
	run_id UUID, 
	role match.message_role NOT NULL, 
	content TEXT NOT NULL, 
	tool_payload JSONB, 
	tokens INTEGER, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT chat_messages_pkey PRIMARY KEY (id), 
	CONSTRAINT chat_messages_content_len CHECK (char_length(content) BETWEEN 1 AND 20000), 
	CONSTRAINT chat_messages_tokens_non_negative CHECK (tokens IS NULL OR tokens >= 0), 
	CONSTRAINT chat_messages_thread_id_fkey FOREIGN KEY(thread_id) REFERENCES match.chat_threads (id) ON DELETE CASCADE, 
	CONSTRAINT chat_messages_run_id_fkey FOREIGN KEY(run_id) REFERENCES match.search_runs (id) ON DELETE SET NULL
);

CREATE TABLE match.search_results (
	run_id UUID NOT NULL, 
	candidate_id UUID NOT NULL, 
	stage match.match_stage NOT NULL, 
	rank SMALLINT, 
	vector_score NUMERIC(6, 5), 
	llm_score NUMERIC(6, 5), 
	final_score NUMERIC(6, 5), 
	explanation VARCHAR(2000), 
	is_shown BOOLEAN DEFAULT 'true' NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	CONSTRAINT search_results_pkey PRIMARY KEY (run_id, candidate_id), 
	CONSTRAINT search_results_rank_uq UNIQUE (run_id, rank), 
	CONSTRAINT search_results_rank_positive CHECK (rank IS NULL OR rank > 0), 
	CONSTRAINT search_results_vector_score_range CHECK (vector_score IS NULL OR vector_score BETWEEN -1 AND 1), 
	CONSTRAINT search_results_llm_score_range CHECK (llm_score IS NULL OR llm_score BETWEEN 0 AND 1), 
	CONSTRAINT search_results_final_score_range CHECK (final_score IS NULL OR final_score BETWEEN 0 AND 1), 
	CONSTRAINT search_results_llm_stage_has_explanation CHECK (stage <> 'llm' OR explanation IS NOT NULL), 
	CONSTRAINT search_results_run_id_fkey FOREIGN KEY(run_id) REFERENCES match.search_runs (id) ON DELETE CASCADE, 
	CONSTRAINT search_results_candidate_id_fkey FOREIGN KEY(candidate_id) REFERENCES profile.candidates (user_id) ON DELETE CASCADE
);

CREATE TABLE profile.github_repo_languages (
	repo_id BIGINT NOT NULL, 
	language VARCHAR(40) NOT NULL, 
	bytes BIGINT NOT NULL, 
	CONSTRAINT github_repo_languages_pkey PRIMARY KEY (repo_id, language), 
	CONSTRAINT github_repo_languages_bytes_positive CHECK (bytes > 0), 
	CONSTRAINT github_repo_languages_repo_id_fkey FOREIGN KEY(repo_id) REFERENCES profile.github_repos (id) ON DELETE CASCADE
);

-- Индексы
CREATE INDEX users_status_idx ON core.users (status);
CREATE INDEX fsp_events_level_idx ON profile.fsp_events (level, starts_on DESC);
CREATE INDEX email_tokens_active_idx ON core.email_tokens (user_id, purpose) WHERE used_at IS NULL;
CREATE INDEX email_tokens_user_id_idx ON core.email_tokens (user_id);
CREATE INDEX refresh_sessions_user_idx ON core.refresh_sessions (user_id) WHERE revoked_at IS NULL;
CREATE UNIQUE INDEX user_consents_active_uq ON core.user_consents (user_id, kind) WHERE revoked_at IS NULL;
CREATE INDEX user_consents_user_id_idx ON core.user_consents (user_id);
CREATE INDEX user_roles_role_idx ON core.user_roles (role);
CREATE INDEX attribute_defs_industry_id_idx ON ref.attribute_defs (industry_id);
CREATE INDEX grades_industry_id_idx ON ref.grades (industry_id);
CREATE INDEX skills_area_id_idx ON ref.skills (area_id);
CREATE INDEX skills_kind_idx ON ref.skills (kind);
CREATE INDEX skills_name_trgm_idx ON ref.skills USING gin (name gin_trgm_ops);
CREATE INDEX specializations_industry_id_idx ON ref.specializations (industry_id);
CREATE INDEX specializations_parent_id_idx ON ref.specializations (parent_id);
CREATE INDEX survey_questions_survey_id_idx ON assess.survey_questions (survey_id);
CREATE INDEX task_templates_skill_id_idx ON assess.task_templates (skill_id);
CREATE INDEX task_templates_target_idx ON assess.task_templates (specialization_id, grade_id) WHERE is_active;
CREATE INDEX employers_company_id_idx ON hire.employers (company_id);
CREATE INDEX skill_relevance_skill_id_idx ON match.skill_relevance (skill_id);
CREATE INDEX attribute_options_attribute_id_idx ON ref.attribute_options (attribute_id);
CREATE INDEX categories_grade_id_idx ON ref.categories (grade_id);
CREATE INDEX skill_aliases_skill_id_idx ON ref.skill_aliases (skill_id);
CREATE INDEX specialization_skills_skill_id_idx ON ref.specialization_skills (skill_id);
CREATE INDEX survey_options_question_id_idx ON assess.survey_options (question_id);
CREATE INDEX vacancies_company_idx ON hire.vacancies (company_id, created_at DESC);
CREATE INDEX vacancies_published_idx ON hire.vacancies (category_id, published_at DESC) WHERE status = 'published';
CREATE INDEX vacancies_search_idx ON hire.vacancies USING gin (search_vector);
CREATE INDEX candidates_category_idx ON profile.candidates (current_category_id) WHERE is_open_to_offers;
CREATE INDEX candidates_category_strength_idx ON profile.candidates (current_category_id, profile_strength DESC);
CREATE INDEX candidates_city_idx ON profile.candidates (city);
CREATE INDEX candidates_search_idx ON profile.candidates USING gin (search_vector);
CREATE INDEX survey_sessions_candidate_idx ON assess.survey_sessions (candidate_id, started_at DESC);
CREATE INDEX tasks_candidate_idx ON assess.tasks (candidate_id, created_at DESC);
CREATE INDEX tasks_template_id_idx ON assess.tasks (template_id);
CREATE INDEX applications_candidate_idx ON hire.applications (candidate_id, created_at DESC);
CREATE INDEX applications_vacancy_idx ON hire.applications (vacancy_id, status);
CREATE INDEX employer_needs_company_idx ON hire.employer_needs (company_id, created_at DESC) WHERE is_active;
CREATE INDEX vacancy_skills_skill_id_idx ON hire.vacancy_skills (skill_id);
CREATE INDEX candidate_embeddings_hnsw_idx ON match.candidate_embeddings USING hnsw (embedding vector_cosine_ops);
CREATE INDEX vacancy_embeddings_hnsw_idx ON match.vacancy_embeddings USING hnsw (embedding vector_cosine_ops);
CREATE INDEX candidate_attribute_values_attribute_id_idx ON profile.candidate_attribute_values (attribute_id);
CREATE INDEX candidate_attribute_values_candidate_id_idx ON profile.candidate_attribute_values (candidate_id);
CREATE INDEX candidate_links_candidate_id_idx ON profile.candidate_links (candidate_id);
CREATE INDEX candidate_skills_skill_idx ON profile.candidate_skills (skill_id, confirmed_level DESC NULLS LAST);
CREATE INDEX fsp_achievements_candidate_id_idx ON profile.fsp_achievements (candidate_id);
CREATE INDEX fsp_achievements_event_id_idx ON profile.fsp_achievements (event_id);
CREATE INDEX fsp_profiles_verification_status_idx ON profile.fsp_profiles (verification_status);
CREATE INDEX github_signals_skill_id_idx ON profile.github_signals (skill_id);
CREATE INDEX resume_documents_candidate_idx ON profile.resume_documents (candidate_id, uploaded_at DESC);
CREATE INDEX resume_documents_pending_idx ON profile.resume_documents (parse_status) WHERE parse_status IN ('pending', 'processing');
CREATE INDEX regular_assignments_candidate_idx ON assess.regular_assignments (candidate_id, issued_at DESC);
CREATE INDEX regular_assignments_company_id_idx ON assess.regular_assignments (company_id);
CREATE INDEX regular_assignments_open_idx ON assess.regular_assignments (due_at) WHERE status IN ('issued', 'viewed');
CREATE INDEX survey_answers_session_id_idx ON assess.survey_answers (session_id);
CREATE INDEX test_sessions_candidate_idx ON assess.test_sessions (candidate_id, created_at DESC);
CREATE INDEX test_sessions_open_idx ON assess.test_sessions (deadline_at) WHERE status IN ('created', 'in_progress');
CREATE INDEX application_events_application_idx ON hire.application_events (application_id, created_at);
CREATE INDEX invitations_candidate_idx ON hire.invitations (candidate_id, status, sent_at DESC);
CREATE INDEX invitations_company_idx ON hire.invitations (company_id, status, sent_at DESC);
CREATE INDEX invitations_need_id_idx ON hire.invitations (need_id);
CREATE INDEX invitations_vacancy_id_idx ON hire.invitations (vacancy_id);
CREATE INDEX need_skills_skill_id_idx ON hire.need_skills (skill_id);
CREATE INDEX chat_threads_need_idx ON match.chat_threads (need_id, updated_at DESC);
CREATE INDEX need_embeddings_hnsw_idx ON match.need_embeddings USING hnsw (embedding vector_cosine_ops);
CREATE INDEX search_runs_need_idx ON match.search_runs (need_id, created_at DESC);
CREATE INDEX search_runs_parent_run_id_idx ON match.search_runs (parent_run_id);
CREATE INDEX github_repos_candidate_id_idx ON profile.github_repos (candidate_id);
CREATE INDEX resume_parse_results_document_id_idx ON profile.resume_parse_results (document_id);
CREATE INDEX grade_assignments_candidate_idx ON assess.grade_assignments (candidate_id, assigned_at DESC);
CREATE INDEX grade_assignments_category_idx ON assess.grade_assignments (category_id) WHERE is_current;
CREATE UNIQUE INDEX grade_assignments_current_uq ON assess.grade_assignments (candidate_id) WHERE is_current;
CREATE INDEX task_answers_session_id_idx ON assess.task_answers (session_id);
CREATE INDEX task_answers_unchecked_idx ON assess.task_answers (submitted_at) WHERE checked_at IS NULL;
CREATE UNIQUE INDEX contact_disclosures_active_uq ON hire.contact_disclosures (candidate_id, company_id) WHERE revoked_at IS NULL;
CREATE INDEX contact_disclosures_company_id_idx ON hire.contact_disclosures (company_id);
CREATE INDEX invitation_events_invitation_idx ON hire.invitation_events (invitation_id, created_at);
CREATE INDEX chat_messages_thread_idx ON match.chat_messages (thread_id, created_at);
CREATE INDEX search_results_candidate_id_idx ON match.search_results (candidate_id);
CREATE INDEX search_results_top_idx ON match.search_results (run_id, final_score DESC) WHERE is_shown;

COMMIT;
