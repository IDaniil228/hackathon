"""initial schema

Revision ID: a81dac834799
Revises: 
Create Date: 2026-10-06 15:09:54.315388
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
# тип vector(N) из pgvector встречается в колонках эмбеддингов
import pgvector.sqlalchemy
from sqlalchemy.dialects import postgresql

revision: str = 'a81dac834799'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- то, чего автогенерация не видит ---------------------------------
    # Схемы: шесть логических модулей, по ним же разложены ENUM-типы.
    op.execute("CREATE SCHEMA IF NOT EXISTS core")
    op.execute("CREATE SCHEMA IF NOT EXISTS ref")
    op.execute("CREATE SCHEMA IF NOT EXISTS profile")
    op.execute("CREATE SCHEMA IF NOT EXISTS assess")
    op.execute("CREATE SCHEMA IF NOT EXISTS hire")
    op.execute("CREATE SCHEMA IF NOT EXISTS match")

    # Расширения: vector — эмбеддинги, citext — регистронезависимые email и
    # синонимы навыков, pg_trgm — автодополнение по подстроке.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")

    # ENUM-типы одним явным проходом: в моделях у них create_type=False,
    # иначе SQLAlchemy пытался бы создать один и тот же тип при каждой
    # использующей его таблице и падал на второй.
    op.execute("CREATE TYPE core.user_status AS ENUM ('pending', 'active', 'blocked')")
    op.execute("CREATE TYPE core.user_role AS ENUM ('candidate', 'employer', 'admin')")
    op.execute("CREATE TYPE core.token_purpose AS ENUM ('verify_email', 'reset_password')")
    op.execute("CREATE TYPE core.consent_kind AS ENUM ('pd_processing', 'profile_publication', 'fsp_data_sync')")
    op.execute("CREATE TYPE ref.skill_kind AS ENUM ('language', 'framework', 'tool', 'practice', 'soft', 'domain')")
    op.execute("CREATE TYPE ref.attribute_scope AS ENUM ('candidate', 'vacancy', 'company', 'task')")
    op.execute("CREATE TYPE ref.attribute_data_type AS ENUM ('string', 'text', 'integer', 'decimal', 'boolean', 'date', 'option')")
    op.execute("CREATE TYPE profile.work_format AS ENUM ('office', 'hybrid', 'remote')")
    op.execute("CREATE TYPE profile.employment_type AS ENUM ('full_time', 'part_time', 'project', 'internship', 'contract')")
    op.execute("CREATE TYPE profile.link_kind AS ENUM ('github', 'gitlab', 'telegram', 'portfolio', 'linkedin', 'habr', 'other')")
    op.execute("CREATE TYPE profile.skill_source AS ENUM ('self', 'resume', 'test', 'github', 'fsp')")
    op.execute("CREATE TYPE profile.resume_kind AS ENUM ('uploaded', 'generated')")
    op.execute("CREATE TYPE profile.parse_status AS ENUM ('pending', 'processing', 'done', 'failed')")
    op.execute("CREATE TYPE profile.fsp_link_status AS ENUM ('unlinked', 'pending', 'verified', 'failed')")
    op.execute("CREATE TYPE profile.fsp_event_level AS ENUM ('local', 'regional', 'federal', 'international')")
    op.execute("CREATE TYPE profile.achievement_source AS ENUM ('registry', 'manual')")
    op.execute("CREATE TYPE assess.question_format AS ENUM ('single_choice', 'multi_choice', 'open', 'code', 'case', 'scale')")
    op.execute("CREATE TYPE assess.template_source AS ENUM ('manual', 'llm', 'imported')")
    op.execute("CREATE TYPE assess.session_status AS ENUM ('created', 'in_progress', 'submitted', 'scored', 'expired', 'cancelled')")
    op.execute("CREATE TYPE assess.session_outcome AS ENUM ('passed', 'failed', 'downgraded', 'upgraded')")
    op.execute("CREATE TYPE assess.checked_by AS ENUM ('auto', 'llm', 'human')")
    op.execute("CREATE TYPE assess.grade_source AS ENUM ('test', 'manual', 'admin', 'import')")
    op.execute("CREATE TYPE assess.assignment_status AS ENUM ('issued', 'viewed', 'answered', 'scored', 'expired', 'skipped')")
    op.execute("CREATE TYPE hire.company_size AS ENUM ('micro', 'small', 'medium', 'large', 'enterprise')")
    op.execute("CREATE TYPE hire.vacancy_status AS ENUM ('draft', 'published', 'paused', 'archived')")
    op.execute("CREATE TYPE hire.skill_importance AS ENUM ('required', 'nice_to_have')")
    op.execute("CREATE TYPE hire.invitation_status AS ENUM ('sent', 'viewed', 'accepted', 'declined', 'withdrawn', 'expired')")
    op.execute("CREATE TYPE hire.application_status AS ENUM ('sent', 'viewed', 'invited', 'rejected', 'withdrawn')")
    op.execute("CREATE TYPE hire.contact_method AS ENUM ('email', 'phone', 'telegram', 'platform')")
    op.execute("CREATE TYPE hire.disclosure_reason AS ENUM ('invitation_accepted', 'application_sent')")
    op.execute("CREATE TYPE match.match_stage AS ENUM ('prefilter', 'vector', 'llm')")
    op.execute("CREATE TYPE match.run_status AS ENUM ('queued', 'running', 'done', 'failed')")
    op.execute("CREATE TYPE match.message_role AS ENUM ('user', 'assistant', 'system', 'tool')")

    # --- таблицы ----------------------------------------------------------
    op.create_table('users',
    sa.Column('id', sa.UUID(), server_default=sa.text('uuidv7()'), nullable=False),
    sa.Column('email', postgresql.CITEXT(), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=True),
    sa.Column('status', postgresql.ENUM('pending', 'active', 'blocked', name='user_status', create_type=False, schema='core'), server_default=sa.text("'pending'"), nullable=False),
    sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('external_subject', sa.String(length=64), nullable=True),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("email ~ '^[^@[:space:]]+@[^@[:space:]]+\\.[A-Za-z]{2,}$'", name=op.f('users_email_format')),
    sa.CheckConstraint("status <> 'active' OR email_verified_at IS NOT NULL", name=op.f('users_active_requires_verified_email')),
    sa.CheckConstraint('char_length(email) BETWEEN 5 AND 254', name=op.f('users_email_len')),
    sa.CheckConstraint('password_hash IS NOT NULL OR external_subject IS NOT NULL', name=op.f('users_auth_method_present')),
    sa.PrimaryKeyConstraint('id', name=op.f('users_pkey')),
    sa.UniqueConstraint('email', name=op.f('users_email_key')),
    sa.UniqueConstraint('external_subject', name=op.f('users_external_subject_key')),
    schema='core'
    )
    op.create_index('users_status_idx', 'users', ['status'], unique=False, schema='core')
    op.create_table('fsp_events',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('external_id', sa.String(length=64), nullable=True),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('discipline', sa.String(length=100), nullable=True),
    sa.Column('level', postgresql.ENUM('local', 'regional', 'federal', 'international', name='fsp_event_level', create_type=False, schema='profile'), nullable=False),
    sa.Column('starts_on', sa.Date(), nullable=True),
    sa.Column('ends_on', sa.Date(), nullable=True),
    sa.CheckConstraint('char_length(btrim(title)) > 0', name=op.f('fsp_events_title_not_blank')),
    sa.CheckConstraint('ends_on IS NULL OR starts_on IS NULL OR ends_on >= starts_on', name=op.f('fsp_events_period')),
    sa.PrimaryKeyConstraint('id', name=op.f('fsp_events_pkey')),
    sa.UniqueConstraint('external_id', name=op.f('fsp_events_external_id_key')),
    schema='profile'
    )
    op.create_index('fsp_events_level_idx', 'fsp_events', ['level', sa.literal_column('starts_on DESC')], unique=False, schema='profile')
    op.create_table('industries',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.String(length=500), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("code ~ '^[a-z][a-z0-9_]{1,39}$'", name=op.f('industries_code_format')),
    sa.CheckConstraint('char_length(btrim(name)) > 0', name=op.f('industries_name_not_blank')),
    sa.PrimaryKeyConstraint('id', name=op.f('industries_pkey')),
    sa.UniqueConstraint('code', name=op.f('industries_code_key')),
    schema='ref'
    )
    op.create_table('skill_areas',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('parent_id', sa.BigInteger(), nullable=True),
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('sort_order', sa.SmallInteger(), server_default='0', nullable=False),
    sa.CheckConstraint("code ~ '^[a-z][a-z0-9_]{1,39}$'", name=op.f('skill_areas_code_format')),
    sa.CheckConstraint('char_length(btrim(name)) > 0', name=op.f('skill_areas_name_not_blank')),
    sa.CheckConstraint('parent_id IS DISTINCT FROM id', name=op.f('skill_areas_no_self_parent')),
    sa.ForeignKeyConstraint(['parent_id'], ['ref.skill_areas.id'], name=op.f('skill_areas_parent_id_fkey'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('skill_areas_pkey')),
    sa.UniqueConstraint('code', name=op.f('skill_areas_code_key')),
    schema='ref'
    )
    op.create_table('survey_templates',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('industry_id', sa.BigInteger(), nullable=False),
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('version', sa.SmallInteger(), server_default='1', nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('version > 0', name=op.f('survey_templates_version_positive')),
    sa.ForeignKeyConstraint(['industry_id'], ['ref.industries.id'], name=op.f('survey_templates_industry_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('survey_templates_pkey')),
    sa.UniqueConstraint('code', name=op.f('survey_templates_code_key')),
    schema='assess'
    )
    op.create_table('email_tokens',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('purpose', postgresql.ENUM('verify_email', 'reset_password', name='token_purpose', create_type=False, schema='core'), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("token_hash ~ '^[a-f0-9]{64}$'", name=op.f('email_tokens_hash_format')),
    sa.CheckConstraint('expires_at > created_at', name=op.f('email_tokens_expires_after_create')),
    sa.CheckConstraint('used_at IS NULL OR used_at >= created_at', name=op.f('email_tokens_used_after_create')),
    sa.ForeignKeyConstraint(['user_id'], ['core.users.id'], name=op.f('email_tokens_user_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('email_tokens_pkey')),
    sa.UniqueConstraint('token_hash', name=op.f('email_tokens_token_hash_key')),
    schema='core'
    )
    op.create_index('email_tokens_active_idx', 'email_tokens', ['user_id', 'purpose'], unique=False, schema='core', postgresql_where=sa.text('used_at IS NULL'))
    op.create_index(op.f('email_tokens_user_id_idx'), 'email_tokens', ['user_id'], unique=False, schema='core')
    op.create_table('refresh_sessions',
    sa.Column('id', sa.UUID(), server_default=sa.text('uuidv7()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('user_agent', sa.String(length=255), nullable=True),
    sa.Column('ip', postgresql.INET(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("token_hash ~ '^[a-f0-9]{64}$'", name=op.f('refresh_sessions_hash_format')),
    sa.CheckConstraint('expires_at > created_at', name=op.f('refresh_sessions_expires_after_create')),
    sa.ForeignKeyConstraint(['user_id'], ['core.users.id'], name=op.f('refresh_sessions_user_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('refresh_sessions_pkey')),
    sa.UniqueConstraint('token_hash', name=op.f('refresh_sessions_token_hash_key')),
    schema='core'
    )
    op.create_index('refresh_sessions_user_idx', 'refresh_sessions', ['user_id'], unique=False, schema='core', postgresql_where=sa.text('revoked_at IS NULL'))
    op.create_table('user_consents',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('kind', postgresql.ENUM('pd_processing', 'profile_publication', 'fsp_data_sync', name='consent_kind', create_type=False, schema='core'), nullable=False),
    sa.Column('document_version', sa.String(length=20), nullable=False),
    sa.Column('granted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('char_length(btrim(document_version)) > 0', name=op.f('user_consents_doc_version_not_blank')),
    sa.CheckConstraint('revoked_at IS NULL OR revoked_at >= granted_at', name=op.f('user_consents_revoked_after_granted')),
    sa.ForeignKeyConstraint(['user_id'], ['core.users.id'], name=op.f('user_consents_user_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('user_consents_pkey')),
    schema='core'
    )
    op.create_index('user_consents_active_uq', 'user_consents', ['user_id', 'kind'], unique=True, schema='core', postgresql_where=sa.text('revoked_at IS NULL'))
    op.create_index(op.f('user_consents_user_id_idx'), 'user_consents', ['user_id'], unique=False, schema='core')
    op.create_table('user_roles',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('role', postgresql.ENUM('candidate', 'employer', 'admin', name='user_role', create_type=False, schema='core'), nullable=False),
    sa.Column('granted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['core.users.id'], name=op.f('user_roles_user_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'role', name=op.f('user_roles_pkey')),
    schema='core'
    )
    op.create_index('user_roles_role_idx', 'user_roles', ['role'], unique=False, schema='core')
    op.create_table('companies',
    sa.Column('id', sa.UUID(), server_default=sa.text('uuidv7()'), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('legal_name', sa.String(length=200), nullable=True),
    sa.Column('inn', sa.String(length=12), nullable=True),
    sa.Column('industry_id', sa.BigInteger(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('website', sa.String(length=2048), nullable=True),
    sa.Column('logo_url', sa.String(length=2048), nullable=True),
    sa.Column('size', postgresql.ENUM('micro', 'small', 'medium', 'large', 'enterprise', name='company_size', create_type=False, schema='hire'), nullable=True),
    sa.Column('city', sa.String(length=100), nullable=True),
    sa.Column('is_verified', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("inn IS NULL OR inn ~ '^[0-9]{10}$' OR inn ~ '^[0-9]{12}$'", name=op.f('companies_inn_format')),
    sa.CheckConstraint("website IS NULL OR website ~ '^https?://[^[:space:]]{3,}$'", name=op.f('companies_website_format')),
    sa.CheckConstraint('char_length(btrim(name)) > 0', name=op.f('companies_name_not_blank')),
    sa.CheckConstraint('char_length(description) <= 5000', name=op.f('companies_description_len')),
    sa.ForeignKeyConstraint(['industry_id'], ['ref.industries.id'], name=op.f('companies_industry_id_fkey'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('companies_pkey')),
    sa.UniqueConstraint('inn', name=op.f('companies_inn_key')),
    schema='hire'
    )
    op.create_table('attribute_defs',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('scope', postgresql.ENUM('candidate', 'vacancy', 'company', 'task', name='attribute_scope', create_type=False, schema='ref'), nullable=False),
    sa.Column('industry_id', sa.BigInteger(), nullable=True),
    sa.Column('code', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('data_type', postgresql.ENUM('string', 'text', 'integer', 'decimal', 'boolean', 'date', 'option', name='attribute_data_type', create_type=False, schema='ref'), nullable=False),
    sa.Column('unit', sa.String(length=20), nullable=True),
    sa.Column('is_multi', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('is_required', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('is_public', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('sort_order', sa.SmallInteger(), server_default='0', nullable=False),
    sa.CheckConstraint("NOT is_multi OR data_type = 'option'", name=op.f('attribute_defs_multi_only_for_option')),
    sa.CheckConstraint("code ~ '^[a-z][a-z0-9_]{1,49}$'", name=op.f('attribute_defs_code_format')),
    sa.CheckConstraint('char_length(btrim(name)) > 0', name=op.f('attribute_defs_name_not_blank')),
    sa.ForeignKeyConstraint(['industry_id'], ['ref.industries.id'], name=op.f('attribute_defs_industry_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('attribute_defs_pkey')),
    sa.UniqueConstraint('id', 'data_type', name='attribute_defs_id_type_uq'),
    sa.UniqueConstraint('scope', 'code', name='attribute_defs_scope_code_uq'),
    schema='ref'
    )
    op.create_index(op.f('attribute_defs_industry_id_idx'), 'attribute_defs', ['industry_id'], unique=False, schema='ref')
    op.create_table('grades',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('industry_id', sa.BigInteger(), nullable=False),
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=50), nullable=False),
    sa.Column('level', sa.SmallInteger(), nullable=False),
    sa.Column('min_experience_months', sa.SmallInteger(), nullable=True),
    sa.Column('cooldown_days', sa.SmallInteger(), server_default='90', nullable=False),
    sa.CheckConstraint("code ~ '^[a-z][a-z0-9_]{1,39}$'", name=op.f('grades_code_format')),
    sa.CheckConstraint('char_length(btrim(name)) > 0', name=op.f('grades_name_not_blank')),
    sa.CheckConstraint('cooldown_days BETWEEN 0 AND 3650', name=op.f('grades_cooldown_range')),
    sa.CheckConstraint('level BETWEEN 0 AND 100', name=op.f('grades_level_range')),
    sa.CheckConstraint('min_experience_months IS NULL OR min_experience_months BETWEEN 0 AND 840', name=op.f('grades_min_experience_range')),
    sa.ForeignKeyConstraint(['industry_id'], ['ref.industries.id'], name=op.f('grades_industry_id_fkey'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('grades_pkey')),
    sa.UniqueConstraint('industry_id', 'code', name='grades_industry_code_uq'),
    sa.UniqueConstraint('industry_id', 'level', name='grades_industry_level_uq'),
    schema='ref'
    )
    op.create_index(op.f('grades_industry_id_idx'), 'grades', ['industry_id'], unique=False, schema='ref')
    op.create_table('skills',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('area_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', postgresql.ENUM('language', 'framework', 'tool', 'practice', 'soft', 'domain', name='skill_kind', create_type=False, schema='ref'), nullable=False),
    sa.Column('code', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.CheckConstraint("code ~ '^[a-z][a-z0-9_+.-]{1,49}$'", name=op.f('skills_code_format')),
    sa.CheckConstraint('char_length(btrim(name)) > 0', name=op.f('skills_name_not_blank')),
    sa.ForeignKeyConstraint(['area_id'], ['ref.skill_areas.id'], name=op.f('skills_area_id_fkey'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('skills_pkey')),
    sa.UniqueConstraint('code', name=op.f('skills_code_key')),
    schema='ref'
    )
    op.create_index(op.f('skills_area_id_idx'), 'skills', ['area_id'], unique=False, schema='ref')
    op.create_index(op.f('skills_kind_idx'), 'skills', ['kind'], unique=False, schema='ref')
    op.create_index('skills_name_trgm_idx', 'skills', ['name'], unique=False, schema='ref', postgresql_using='gin', postgresql_ops={'name': 'gin_trgm_ops'})
    op.create_table('specializations',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('industry_id', sa.BigInteger(), nullable=False),
    sa.Column('parent_id', sa.BigInteger(), nullable=True),
    sa.Column('code', sa.String(length=40), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.String(length=500), nullable=True),
    sa.Column('sort_order', sa.SmallInteger(), server_default='0', nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.CheckConstraint("code ~ '^[a-z][a-z0-9_]{1,39}$'", name=op.f('specializations_code_format')),
    sa.CheckConstraint('char_length(btrim(name)) > 0', name=op.f('specializations_name_not_blank')),
    sa.CheckConstraint('parent_id IS DISTINCT FROM id', name=op.f('specializations_no_self_parent')),
    sa.ForeignKeyConstraint(['industry_id'], ['ref.industries.id'], name=op.f('specializations_industry_id_fkey'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['parent_id'], ['ref.specializations.id'], name=op.f('specializations_parent_id_fkey'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('specializations_pkey')),
    sa.UniqueConstraint('industry_id', 'code', name='specializations_industry_code_uq'),
    schema='ref'
    )
    op.create_index(op.f('specializations_industry_id_idx'), 'specializations', ['industry_id'], unique=False, schema='ref')
    op.create_index(op.f('specializations_parent_id_idx'), 'specializations', ['parent_id'], unique=False, schema='ref')
    op.create_table('survey_questions',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('survey_id', sa.BigInteger(), nullable=False),
    sa.Column('order_no', sa.SmallInteger(), nullable=False),
    sa.Column('format', postgresql.ENUM('single_choice', 'multi_choice', 'open', 'code', 'case', 'scale', name='question_format', create_type=False, schema='assess'), nullable=False),
    sa.Column('text', sa.String(length=500), nullable=False),
    sa.Column('is_required', sa.Boolean(), server_default='true', nullable=False),
    sa.CheckConstraint('char_length(btrim(text)) > 0', name=op.f('survey_questions_text_not_blank')),
    sa.CheckConstraint('order_no > 0', name=op.f('survey_questions_order_positive')),
    sa.ForeignKeyConstraint(['survey_id'], ['assess.survey_templates.id'], name=op.f('survey_questions_survey_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('survey_questions_pkey')),
    sa.UniqueConstraint('survey_id', 'order_no', name='survey_questions_order_uq'),
    schema='assess'
    )
    op.create_index(op.f('survey_questions_survey_id_idx'), 'survey_questions', ['survey_id'], unique=False, schema='assess')
    op.create_table('task_templates',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('specialization_id', sa.BigInteger(), nullable=False),
    sa.Column('grade_id', sa.BigInteger(), nullable=False),
    sa.Column('skill_id', sa.BigInteger(), nullable=True),
    sa.Column('code', sa.String(length=50), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('format', postgresql.ENUM('single_choice', 'multi_choice', 'open', 'code', 'case', 'scale', name='question_format', create_type=False, schema='assess'), nullable=False),
    sa.Column('difficulty', sa.SmallInteger(), nullable=False),
    sa.Column('expected_minutes', sa.SmallInteger(), nullable=True),
    sa.Column('max_score', sa.Numeric(precision=6, scale=2), server_default='1', nullable=False),
    sa.Column('generator_prompt', sa.Text(), nullable=True),
    sa.Column('grading_rubric', sa.Text(), nullable=True),
    sa.Column('params_schema', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('source', postgresql.ENUM('manual', 'llm', 'imported', name='template_source', create_type=False, schema='assess'), server_default=sa.text("'manual'"), nullable=False),
    sa.Column('version', sa.SmallInteger(), server_default='1', nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('usage_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('avg_score', sa.Numeric(precision=5, scale=2), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('avg_score IS NULL OR avg_score >= 0', name=op.f('task_templates_avg_score_non_negative')),
    sa.CheckConstraint('char_length(generator_prompt) <= 8000', name=op.f('task_templates_gen_prompt_len')),
    sa.CheckConstraint('char_length(grading_rubric) <= 8000', name=op.f('task_templates_rubric_len')),
    sa.CheckConstraint('difficulty BETWEEN 1 AND 10', name=op.f('task_templates_difficulty_range')),
    sa.CheckConstraint('expected_minutes IS NULL OR expected_minutes BETWEEN 1 AND 240', name=op.f('task_templates_minutes_range')),
    sa.CheckConstraint('max_score > 0', name=op.f('task_templates_max_score_positive')),
    sa.CheckConstraint('usage_count >= 0', name=op.f('task_templates_usage_non_negative')),
    sa.CheckConstraint('version > 0', name=op.f('task_templates_version_positive')),
    sa.ForeignKeyConstraint(['grade_id'], ['ref.grades.id'], name=op.f('task_templates_grade_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['skill_id'], ['ref.skills.id'], name=op.f('task_templates_skill_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['specialization_id'], ['ref.specializations.id'], name=op.f('task_templates_specialization_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('task_templates_pkey')),
    sa.UniqueConstraint('code', name=op.f('task_templates_code_key')),
    schema='assess'
    )
    op.create_index(op.f('task_templates_skill_id_idx'), 'task_templates', ['skill_id'], unique=False, schema='assess')
    op.create_index('task_templates_target_idx', 'task_templates', ['specialization_id', 'grade_id'], unique=False, schema='assess', postgresql_where=sa.text('is_active'))
    op.create_table('employers',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('company_id', sa.UUID(), nullable=False),
    sa.Column('full_name', sa.String(length=120), nullable=False),
    sa.Column('position', sa.String(length=100), nullable=True),
    sa.Column('contact_email', postgresql.CITEXT(), nullable=True),
    sa.Column('contact_phone', sa.String(length=20), nullable=True),
    sa.Column('is_owner', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("contact_email IS NULL OR contact_email ~ '^[^@[:space:]]+@[^@[:space:]]+\\.[A-Za-z]{2,}$'", name=op.f('employers_contact_email_format')),
    sa.CheckConstraint("contact_phone IS NULL OR contact_phone ~ '^\\+?[0-9]{7,15}$'", name=op.f('employers_contact_phone_format')),
    sa.CheckConstraint('char_length(btrim(full_name)) > 0', name=op.f('employers_full_name_not_blank')),
    sa.ForeignKeyConstraint(['company_id'], ['hire.companies.id'], name=op.f('employers_company_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['core.users.id'], name=op.f('employers_user_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', name=op.f('employers_pkey')),
    schema='hire'
    )
    op.create_index(op.f('employers_company_id_idx'), 'employers', ['company_id'], unique=False, schema='hire')
    op.create_table('skill_relevance',
    sa.Column('specialization_id', sa.BigInteger(), nullable=False),
    sa.Column('skill_id', sa.BigInteger(), nullable=False),
    sa.Column('need_mentions', sa.Integer(), server_default='0', nullable=False),
    sa.Column('need_total', sa.Integer(), server_default='0', nullable=False),
    sa.Column('candidate_mentions', sa.Integer(), server_default='0', nullable=False),
    sa.Column('weight', sa.Numeric(precision=4, scale=3), nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('candidate_mentions >= 0', name=op.f('skill_relevance_candidate_mentions_non_negative')),
    sa.CheckConstraint('need_mentions >= 0', name=op.f('skill_relevance_need_mentions_non_negative')),
    sa.CheckConstraint('need_total >= need_mentions', name=op.f('skill_relevance_mentions_within_total')),
    sa.CheckConstraint('weight BETWEEN 0 AND 1', name=op.f('skill_relevance_weight_range')),
    sa.ForeignKeyConstraint(['skill_id'], ['ref.skills.id'], name=op.f('skill_relevance_skill_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['specialization_id'], ['ref.specializations.id'], name=op.f('skill_relevance_specialization_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('specialization_id', 'skill_id', name=op.f('skill_relevance_pkey')),
    schema='match'
    )
    op.create_index(op.f('skill_relevance_skill_id_idx'), 'skill_relevance', ['skill_id'], unique=False, schema='match')
    op.create_table('attribute_options',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('attribute_id', sa.BigInteger(), nullable=False),
    sa.Column('code', sa.String(length=50), nullable=False),
    sa.Column('label', sa.String(length=100), nullable=False),
    sa.Column('sort_order', sa.SmallInteger(), server_default='0', nullable=False),
    sa.CheckConstraint("code ~ '^[a-z][a-z0-9_]{1,49}$'", name=op.f('attribute_options_code_format')),
    sa.CheckConstraint('char_length(btrim(label)) > 0', name=op.f('attribute_options_label_not_blank')),
    sa.ForeignKeyConstraint(['attribute_id'], ['ref.attribute_defs.id'], name=op.f('attribute_options_attribute_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('attribute_options_pkey')),
    sa.UniqueConstraint('attribute_id', 'code', name='attribute_options_attr_code_uq'),
    sa.UniqueConstraint('attribute_id', 'id', name='attribute_options_attr_id_uq'),
    schema='ref'
    )
    op.create_index(op.f('attribute_options_attribute_id_idx'), 'attribute_options', ['attribute_id'], unique=False, schema='ref')
    op.create_table('categories',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('specialization_id', sa.BigInteger(), nullable=False),
    sa.Column('grade_id', sa.BigInteger(), nullable=False),
    sa.Column('slug', sa.String(length=90), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.CheckConstraint("slug ~ '^[a-z][a-z0-9_.-]{1,89}$'", name=op.f('categories_slug_format')),
    sa.ForeignKeyConstraint(['grade_id'], ['ref.grades.id'], name=op.f('categories_grade_id_fkey'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['specialization_id'], ['ref.specializations.id'], name=op.f('categories_specialization_id_fkey'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('categories_pkey')),
    sa.UniqueConstraint('slug', name=op.f('categories_slug_key')),
    sa.UniqueConstraint('specialization_id', 'grade_id', name='categories_spec_grade_uq'),
    schema='ref'
    )
    op.create_index(op.f('categories_grade_id_idx'), 'categories', ['grade_id'], unique=False, schema='ref')
    op.create_table('skill_aliases',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('skill_id', sa.BigInteger(), nullable=False),
    sa.Column('alias', postgresql.CITEXT(), nullable=False),
    sa.CheckConstraint('char_length(alias) BETWEEN 1 AND 60', name=op.f('skill_aliases_len')),
    sa.ForeignKeyConstraint(['skill_id'], ['ref.skills.id'], name=op.f('skill_aliases_skill_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('skill_aliases_pkey')),
    sa.UniqueConstraint('alias', name=op.f('skill_aliases_alias_key')),
    schema='ref'
    )
    op.create_index(op.f('skill_aliases_skill_id_idx'), 'skill_aliases', ['skill_id'], unique=False, schema='ref')
    op.create_table('specialization_skills',
    sa.Column('specialization_id', sa.BigInteger(), nullable=False),
    sa.Column('skill_id', sa.BigInteger(), nullable=False),
    sa.Column('is_core', sa.Boolean(), server_default='false', nullable=False),
    sa.ForeignKeyConstraint(['skill_id'], ['ref.skills.id'], name=op.f('specialization_skills_skill_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['specialization_id'], ['ref.specializations.id'], name=op.f('specialization_skills_specialization_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('specialization_id', 'skill_id', name=op.f('specialization_skills_pkey')),
    schema='ref'
    )
    op.create_index(op.f('specialization_skills_skill_id_idx'), 'specialization_skills', ['skill_id'], unique=False, schema='ref')
    op.create_table('survey_options',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('question_id', sa.BigInteger(), nullable=False),
    sa.Column('order_no', sa.SmallInteger(), nullable=False),
    sa.Column('label', sa.String(length=200), nullable=False),
    sa.Column('specialization_id', sa.BigInteger(), nullable=True),
    sa.Column('skill_id', sa.BigInteger(), nullable=True),
    sa.Column('weight', sa.Numeric(precision=4, scale=2), server_default='1.00', nullable=False),
    sa.CheckConstraint('order_no > 0', name=op.f('survey_options_order_positive')),
    sa.ForeignKeyConstraint(['question_id'], ['assess.survey_questions.id'], name=op.f('survey_options_question_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['skill_id'], ['ref.skills.id'], name=op.f('survey_options_skill_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['specialization_id'], ['ref.specializations.id'], name=op.f('survey_options_specialization_id_fkey'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('survey_options_pkey')),
    sa.UniqueConstraint('question_id', 'order_no', name='survey_options_order_uq'),
    schema='assess'
    )
    op.create_index(op.f('survey_options_question_id_idx'), 'survey_options', ['question_id'], unique=False, schema='assess')
    op.create_table('vacancies',
    sa.Column('id', sa.UUID(), server_default=sa.text('uuidv7()'), nullable=False),
    sa.Column('company_id', sa.UUID(), nullable=False),
    sa.Column('author_user_id', sa.UUID(), nullable=True),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('category_id', sa.BigInteger(), nullable=False),
    sa.Column('work_format', postgresql.ENUM('office', 'hybrid', 'remote', name='work_format', create_type=False, schema='profile'), nullable=True),
    sa.Column('employment_type', postgresql.ENUM('full_time', 'part_time', 'project', 'internship', 'contract', name='employment_type', create_type=False, schema='profile'), nullable=True),
    sa.Column('city', sa.String(length=100), nullable=True),
    sa.Column('salary_min', sa.Integer(), nullable=False),
    sa.Column('salary_max', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=3), server_default=sa.text("'RUB'"), nullable=False),
    sa.Column('status', postgresql.ENUM('draft', 'published', 'paused', 'archived', name='vacancy_status', create_type=False, schema='hire'), server_default=sa.text("'draft'"), nullable=False),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('search_vector', postgresql.TSVECTOR(), sa.Computed("to_tsvector('russian', coalesce(title, '') || ' ' || coalesce(description, ''))", persisted=True), nullable=True),
    sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f('vacancies_currency_format')),
    sa.CheckConstraint("status <> 'published' OR published_at IS NOT NULL", name=op.f('vacancies_published_has_ts')),
    sa.CheckConstraint('char_length(btrim(title)) > 0', name=op.f('vacancies_title_not_blank')),
    sa.CheckConstraint('char_length(description) BETWEEN 1 AND 20000', name=op.f('vacancies_description_len')),
    sa.CheckConstraint('salary_max > 0 AND salary_max <= 100000000', name=op.f('vacancies_salary_max_range')),
    sa.CheckConstraint('salary_max >= salary_min', name=op.f('vacancies_salary_range')),
    sa.CheckConstraint('salary_min > 0 AND salary_min <= 100000000', name=op.f('vacancies_salary_min_range')),
    sa.ForeignKeyConstraint(['author_user_id'], ['core.users.id'], name=op.f('vacancies_author_user_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['category_id'], ['ref.categories.id'], name=op.f('vacancies_category_id_fkey'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['company_id'], ['hire.companies.id'], name=op.f('vacancies_company_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('vacancies_pkey')),
    schema='hire'
    )
    op.create_index('vacancies_company_idx', 'vacancies', ['company_id', sa.literal_column('created_at DESC')], unique=False, schema='hire')
    op.create_index('vacancies_published_idx', 'vacancies', ['category_id', sa.literal_column('published_at DESC')], unique=False, schema='hire', postgresql_where=sa.text("status = 'published'"))
    op.create_index('vacancies_search_idx', 'vacancies', ['search_vector'], unique=False, schema='hire', postgresql_using='gin')
    op.create_table('candidates',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('first_name', sa.String(length=50), nullable=False),
    sa.Column('last_name', sa.String(length=50), nullable=False),
    sa.Column('middle_name', sa.String(length=50), nullable=True),
    sa.Column('birth_date', sa.Date(), nullable=True),
    sa.Column('phone', sa.String(length=20), nullable=True),
    sa.Column('city', sa.String(length=100), nullable=True),
    sa.Column('relocation', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('work_format', postgresql.ENUM('office', 'hybrid', 'remote', name='work_format', create_type=False, schema='profile'), nullable=True),
    sa.Column('employment_type', postgresql.ENUM('full_time', 'part_time', 'project', 'internship', 'contract', name='employment_type', create_type=False, schema='profile'), nullable=True),
    sa.Column('experience_months', sa.SmallInteger(), server_default='0', nullable=False),
    sa.Column('about', sa.Text(), nullable=True),
    sa.Column('current_category_id', sa.BigInteger(), nullable=True),
    sa.Column('is_open_to_offers', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('profile_strength', sa.Numeric(precision=5, scale=2), server_default='0', nullable=False),
    sa.Column('profile_refreshed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('search_vector', postgresql.TSVECTOR(), sa.Computed("to_tsvector('russian', coalesce(first_name, '') || ' ' || coalesce(last_name, '') || ' ' || coalesce(city, '') || ' ' || coalesce(about, ''))", persisted=True), nullable=True),
    sa.CheckConstraint("birth_date IS NULL OR birth_date BETWEEN DATE '1930-01-01' AND DATE '2020-01-01'", name=op.f('candidates_birth_date_sane')),
    sa.CheckConstraint("phone IS NULL OR phone ~ '^\\+?[0-9]{7,15}$'", name=op.f('candidates_phone_format')),
    sa.CheckConstraint('char_length(about) <= 4000', name=op.f('candidates_about_len')),
    sa.CheckConstraint('char_length(btrim(first_name)) > 0', name=op.f('candidates_first_name_not_blank')),
    sa.CheckConstraint('char_length(btrim(last_name)) > 0', name=op.f('candidates_last_name_not_blank')),
    sa.CheckConstraint('experience_months BETWEEN 0 AND 840', name=op.f('candidates_experience_range')),
    sa.CheckConstraint('profile_strength BETWEEN 0 AND 100', name=op.f('candidates_strength_range')),
    sa.ForeignKeyConstraint(['current_category_id'], ['ref.categories.id'], name=op.f('candidates_current_category_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['core.users.id'], name=op.f('candidates_user_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', name=op.f('candidates_pkey')),
    schema='profile'
    )
    op.create_index('candidates_category_idx', 'candidates', ['current_category_id'], unique=False, schema='profile', postgresql_where=sa.text('is_open_to_offers'))
    op.create_index('candidates_category_strength_idx', 'candidates', ['current_category_id', sa.literal_column('profile_strength DESC')], unique=False, schema='profile')
    op.create_index(op.f('candidates_city_idx'), 'candidates', ['city'], unique=False, schema='profile')
    op.create_index('candidates_search_idx', 'candidates', ['search_vector'], unique=False, schema='profile', postgresql_using='gin')
    op.create_table('survey_sessions',
    sa.Column('id', sa.UUID(), server_default=sa.text('uuidv7()'), nullable=False),
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('survey_id', sa.BigInteger(), nullable=False),
    sa.Column('status', postgresql.ENUM('created', 'in_progress', 'submitted', 'scored', 'expired', 'cancelled', name='session_status', create_type=False, schema='assess'), server_default=sa.text("'created'"), nullable=False),
    sa.Column('specialization_id', sa.BigInteger(), nullable=True),
    sa.Column('claimed_grade_id', sa.BigInteger(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('finished_at IS NULL OR finished_at >= started_at', name=op.f('survey_sessions_finish_after_start')),
    sa.ForeignKeyConstraint(['candidate_id'], ['profile.candidates.user_id'], name=op.f('survey_sessions_candidate_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['claimed_grade_id'], ['ref.grades.id'], name=op.f('survey_sessions_claimed_grade_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['specialization_id'], ['ref.specializations.id'], name=op.f('survey_sessions_specialization_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['survey_id'], ['assess.survey_templates.id'], name=op.f('survey_sessions_survey_id_fkey'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('survey_sessions_pkey')),
    schema='assess'
    )
    op.create_index('survey_sessions_candidate_idx', 'survey_sessions', ['candidate_id', sa.literal_column('started_at DESC')], unique=False, schema='assess')
    op.create_table('tasks',
    sa.Column('id', sa.UUID(), server_default=sa.text('uuidv7()'), nullable=False),
    sa.Column('template_id', sa.BigInteger(), nullable=False),
    sa.Column('candidate_id', sa.UUID(), nullable=True),
    sa.Column('statement', sa.Text(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('answer_key', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('difficulty', sa.SmallInteger(), nullable=False),
    sa.Column('max_score', sa.Numeric(precision=6, scale=2), nullable=False),
    sa.Column('generated_by', sa.String(length=60), nullable=True),
    sa.Column('generation_seed', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('char_length(statement) BETWEEN 1 AND 20000', name=op.f('tasks_statement_len')),
    sa.CheckConstraint('difficulty BETWEEN 1 AND 10', name=op.f('tasks_difficulty_range')),
    sa.CheckConstraint('max_score > 0', name=op.f('tasks_max_score_positive')),
    sa.ForeignKeyConstraint(['candidate_id'], ['profile.candidates.user_id'], name=op.f('tasks_candidate_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['template_id'], ['assess.task_templates.id'], name=op.f('tasks_template_id_fkey'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('tasks_pkey')),
    schema='assess'
    )
    op.create_index('tasks_candidate_idx', 'tasks', ['candidate_id', sa.literal_column('created_at DESC')], unique=False, schema='assess')
    op.create_index(op.f('tasks_template_id_idx'), 'tasks', ['template_id'], unique=False, schema='assess')
    op.create_table('applications',
    sa.Column('id', sa.UUID(), server_default=sa.text('uuidv7()'), nullable=False),
    sa.Column('vacancy_id', sa.UUID(), nullable=False),
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('cover_letter', sa.Text(), nullable=True),
    sa.Column('status', postgresql.ENUM('sent', 'viewed', 'invited', 'rejected', 'withdrawn', name='application_status', create_type=False, schema='hire'), server_default=sa.text("'sent'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('viewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('char_length(cover_letter) <= 5000', name=op.f('applications_cover_letter_len')),
    sa.ForeignKeyConstraint(['candidate_id'], ['profile.candidates.user_id'], name=op.f('applications_candidate_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['vacancy_id'], ['hire.vacancies.id'], name=op.f('applications_vacancy_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('applications_pkey')),
    sa.UniqueConstraint('vacancy_id', 'candidate_id', name='applications_unique'),
    schema='hire'
    )
    op.create_index('applications_candidate_idx', 'applications', ['candidate_id', sa.literal_column('created_at DESC')], unique=False, schema='hire')
    op.create_index('applications_vacancy_idx', 'applications', ['vacancy_id', 'status'], unique=False, schema='hire')
    op.create_table('employer_needs',
    sa.Column('id', sa.UUID(), server_default=sa.text('uuidv7()'), nullable=False),
    sa.Column('company_id', sa.UUID(), nullable=False),
    sa.Column('author_user_id', sa.UUID(), nullable=True),
    sa.Column('vacancy_id', sa.UUID(), nullable=True),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('specialization_id', sa.BigInteger(), nullable=True),
    sa.Column('grade_id', sa.BigInteger(), nullable=True),
    sa.Column('category_id', sa.BigInteger(), nullable=True),
    sa.Column('positive_prompt', sa.String(length=2000), nullable=True),
    sa.Column('negative_prompt', sa.String(length=2000), nullable=True),
    sa.Column('salary_min', sa.Integer(), nullable=True),
    sa.Column('salary_max', sa.Integer(), nullable=True),
    sa.Column('currency', sa.String(length=3), server_default=sa.text("'RUB'"), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f('employer_needs_currency_format')),
    sa.CheckConstraint('char_length(description) BETWEEN 1 AND 20000', name=op.f('employer_needs_description_len')),
    sa.CheckConstraint('salary_max IS NULL OR salary_max > 0', name=op.f('employer_needs_salary_max_positive')),
    sa.CheckConstraint('salary_max IS NULL OR salary_min IS NULL OR salary_max >= salary_min', name=op.f('employer_needs_salary_range')),
    sa.CheckConstraint('salary_min IS NULL OR salary_min > 0', name=op.f('employer_needs_salary_min_positive')),
    sa.ForeignKeyConstraint(['author_user_id'], ['core.users.id'], name=op.f('employer_needs_author_user_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['category_id'], ['ref.categories.id'], name=op.f('employer_needs_category_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['company_id'], ['hire.companies.id'], name=op.f('employer_needs_company_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['grade_id'], ['ref.grades.id'], name=op.f('employer_needs_grade_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['specialization_id'], ['ref.specializations.id'], name=op.f('employer_needs_specialization_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['vacancy_id'], ['hire.vacancies.id'], name=op.f('employer_needs_vacancy_id_fkey'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('employer_needs_pkey')),
    schema='hire'
    )
    op.create_index('employer_needs_company_idx', 'employer_needs', ['company_id', sa.literal_column('created_at DESC')], unique=False, schema='hire', postgresql_where=sa.text('is_active'))
    op.create_table('vacancy_skills',
    sa.Column('vacancy_id', sa.UUID(), nullable=False),
    sa.Column('skill_id', sa.BigInteger(), nullable=False),
    sa.Column('importance', postgresql.ENUM('required', 'nice_to_have', name='skill_importance', create_type=False, schema='hire'), server_default=sa.text("'required'"), nullable=False),
    sa.Column('min_level', sa.Integer(), nullable=True),
    sa.CheckConstraint('min_level IS NULL OR min_level BETWEEN 0 AND 5', name=op.f('vacancy_skills_min_level_range')),
    sa.ForeignKeyConstraint(['skill_id'], ['ref.skills.id'], name=op.f('vacancy_skills_skill_id_fkey'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['vacancy_id'], ['hire.vacancies.id'], name=op.f('vacancy_skills_vacancy_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('vacancy_id', 'skill_id', name=op.f('vacancy_skills_pkey')),
    schema='hire'
    )
    op.create_index(op.f('vacancy_skills_skill_id_idx'), 'vacancy_skills', ['skill_id'], unique=False, schema='hire')
    op.create_table('candidate_embeddings',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('model', sa.String(length=60), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=False),
    sa.Column('source_hash', sa.String(length=64), nullable=False),
    sa.Column('built_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['profile.candidates.user_id'], name=op.f('candidate_embeddings_candidate_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('candidate_embeddings_pkey')),
    sa.UniqueConstraint('candidate_id', 'model', name='candidate_embeddings_uq'),
    schema='match'
    )
    op.create_index('candidate_embeddings_hnsw_idx', 'candidate_embeddings', ['embedding'], unique=False, schema='match', postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.create_table('vacancy_embeddings',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('vacancy_id', sa.UUID(), nullable=False),
    sa.Column('model', sa.String(length=60), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=False),
    sa.Column('source_hash', sa.String(length=64), nullable=False),
    sa.Column('built_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['vacancy_id'], ['hire.vacancies.id'], name=op.f('vacancy_embeddings_vacancy_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('vacancy_embeddings_pkey')),
    sa.UniqueConstraint('vacancy_id', 'model', name='vacancy_embeddings_uq'),
    schema='match'
    )
    op.create_index('vacancy_embeddings_hnsw_idx', 'vacancy_embeddings', ['embedding'], unique=False, schema='match', postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.create_table('candidate_attribute_values',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('attribute_id', sa.BigInteger(), nullable=False),
    sa.Column('data_type', postgresql.ENUM('string', 'text', 'integer', 'decimal', 'boolean', 'date', 'option', name='attribute_data_type', create_type=False, schema='ref'), nullable=False),
    sa.Column('value_string', sa.String(length=255), nullable=True),
    sa.Column('value_text', sa.Text(), nullable=True),
    sa.Column('value_integer', sa.Integer(), nullable=True),
    sa.Column('value_decimal', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('value_boolean', sa.Boolean(), nullable=True),
    sa.Column('value_date', sa.Date(), nullable=True),
    sa.Column('option_id', sa.BigInteger(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(data_type = 'string'  AND value_string  IS NOT NULL) OR (data_type = 'text'    AND value_text    IS NOT NULL) OR (data_type = 'integer' AND value_integer IS NOT NULL) OR (data_type = 'decimal' AND value_decimal IS NOT NULL) OR (data_type = 'boolean' AND value_boolean IS NOT NULL) OR (data_type = 'date'    AND value_date    IS NOT NULL) OR (data_type = 'option'  AND option_id     IS NOT NULL)", name=op.f('candidate_attribute_values_cav_value_matches_type')),
    sa.CheckConstraint('(value_string  IS NOT NULL)::int + (value_text    IS NOT NULL)::int + (value_integer IS NOT NULL)::int + (value_decimal IS NOT NULL)::int + (value_boolean IS NOT NULL)::int + (value_date    IS NOT NULL)::int + (option_id     IS NOT NULL)::int = 1', name=op.f('candidate_attribute_values_cav_exactly_one_value')),
    sa.CheckConstraint('char_length(value_text) <= 4000', name=op.f('candidate_attribute_values_cav_text_len')),
    sa.ForeignKeyConstraint(['attribute_id', 'data_type'], ['ref.attribute_defs.id', 'ref.attribute_defs.data_type'], name='cav_attribute_fk', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['attribute_id', 'option_id'], ['ref.attribute_options.attribute_id', 'ref.attribute_options.id'], name='cav_option_fk', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['candidate_id'], ['profile.candidates.user_id'], name=op.f('candidate_attribute_values_candidate_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('candidate_attribute_values_pkey')),
    sa.UniqueConstraint('candidate_id', 'attribute_id', 'option_id', name='cav_unique_value', postgresql_nulls_not_distinct=True),
    schema='profile'
    )
    op.create_index(op.f('candidate_attribute_values_attribute_id_idx'), 'candidate_attribute_values', ['attribute_id'], unique=False, schema='profile')
    op.create_index(op.f('candidate_attribute_values_candidate_id_idx'), 'candidate_attribute_values', ['candidate_id'], unique=False, schema='profile')
    op.create_table('candidate_links',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('kind', postgresql.ENUM('github', 'gitlab', 'telegram', 'portfolio', 'linkedin', 'habr', 'other', name='link_kind', create_type=False, schema='profile'), nullable=False),
    sa.Column('url', sa.String(length=2048), nullable=False),
    sa.Column('label', sa.String(length=60), nullable=True),
    sa.Column('is_public', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("url ~ '^https?://[^[:space:]]{3,}$'", name=op.f('candidate_links_url_format')),
    sa.ForeignKeyConstraint(['candidate_id'], ['profile.candidates.user_id'], name=op.f('candidate_links_candidate_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('candidate_links_pkey')),
    sa.UniqueConstraint('candidate_id', 'kind', 'url', name='candidate_links_unique'),
    schema='profile'
    )
    op.create_index(op.f('candidate_links_candidate_id_idx'), 'candidate_links', ['candidate_id'], unique=False, schema='profile')
    op.create_table('candidate_preferences',
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('desired_specialization_id', sa.BigInteger(), nullable=True),
    sa.Column('salary_min', sa.Integer(), nullable=True),
    sa.Column('salary_max', sa.Integer(), nullable=True),
    sa.Column('currency', sa.String(length=3), server_default=sa.text("'RUB'"), nullable=False),
    sa.Column('positive_prompt', sa.String(length=1000), nullable=True),
    sa.Column('negative_prompt', sa.String(length=1000), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f('candidate_preferences_currency_format')),
    sa.CheckConstraint('salary_max IS NULL OR (salary_max > 0 AND salary_max <= 100000000)', name=op.f('candidate_preferences_salary_max_range')),
    sa.CheckConstraint('salary_max IS NULL OR salary_min IS NULL OR salary_max >= salary_min', name=op.f('candidate_preferences_salary_range')),
    sa.CheckConstraint('salary_min IS NULL OR (salary_min > 0 AND salary_min <= 100000000)', name=op.f('candidate_preferences_salary_min_range')),
    sa.ForeignKeyConstraint(['candidate_id'], ['profile.candidates.user_id'], name=op.f('candidate_preferences_candidate_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['desired_specialization_id'], ['ref.specializations.id'], name=op.f('candidate_preferences_desired_specialization_id_fkey'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('candidate_id', name=op.f('candidate_preferences_pkey')),
    schema='profile'
    )
    op.create_table('candidate_privacy',
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('is_searchable', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('show_contacts', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('show_salary', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('show_fsp', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('show_github', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['candidate_id'], ['profile.candidates.user_id'], name=op.f('candidate_privacy_candidate_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('candidate_id', name=op.f('candidate_privacy_pkey')),
    schema='profile'
    )
    op.create_table('candidate_skills',
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('skill_id', sa.BigInteger(), nullable=False),
    sa.Column('self_level', sa.SmallInteger(), server_default='0', nullable=False),
    sa.Column('confirmed_level', sa.SmallInteger(), nullable=True),
    sa.Column('years', sa.Numeric(precision=3, scale=1), nullable=True),
    sa.Column('source', postgresql.ENUM('self', 'resume', 'test', 'github', 'fsp', name='skill_source', create_type=False, schema='profile'), server_default=sa.text("'self'"), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('confirmed_level IS NULL OR confirmed_level BETWEEN 0 AND 5', name=op.f('candidate_skills_confirmed_level_range')),
    sa.CheckConstraint('self_level BETWEEN 0 AND 5', name=op.f('candidate_skills_self_level_range')),
    sa.CheckConstraint('years IS NULL OR (years >= 0 AND years <= 70)', name=op.f('candidate_skills_years_range')),
    sa.ForeignKeyConstraint(['candidate_id'], ['profile.candidates.user_id'], name=op.f('candidate_skills_candidate_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['skill_id'], ['ref.skills.id'], name=op.f('candidate_skills_skill_id_fkey'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('candidate_id', 'skill_id', name=op.f('candidate_skills_pkey')),
    schema='profile'
    )
    op.create_index('candidate_skills_skill_idx', 'candidate_skills', ['skill_id', sa.literal_column('confirmed_level DESC NULLS LAST')], unique=False, schema='profile')
    op.create_table('fsp_achievements',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('event_id', sa.BigInteger(), nullable=False),
    sa.Column('place', sa.SmallInteger(), nullable=True),
    sa.Column('participants', sa.Integer(), nullable=True),
    sa.Column('team_name', sa.String(length=100), nullable=True),
    sa.Column('score', sa.Numeric(precision=10, scale=3), nullable=True),
    sa.Column('source', postgresql.ENUM('registry', 'manual', name='achievement_source', create_type=False, schema='profile'), server_default=sa.text("'registry'"), nullable=False),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('raw', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.CheckConstraint('participants IS NULL OR participants > 0', name=op.f('fsp_achievements_participants_positive')),
    sa.CheckConstraint('participants IS NULL OR place IS NULL OR place <= participants', name=op.f('fsp_achievements_place_within_participants')),
    sa.CheckConstraint('place IS NULL OR (place > 0 AND place <= 10000)', name=op.f('fsp_achievements_place_range')),
    sa.ForeignKeyConstraint(['candidate_id'], ['profile.candidates.user_id'], name=op.f('fsp_achievements_candidate_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['event_id'], ['profile.fsp_events.id'], name=op.f('fsp_achievements_event_id_fkey'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('fsp_achievements_pkey')),
    sa.UniqueConstraint('candidate_id', 'event_id', name='fsp_achievements_unique'),
    schema='profile'
    )
    op.create_index(op.f('fsp_achievements_candidate_id_idx'), 'fsp_achievements', ['candidate_id'], unique=False, schema='profile')
    op.create_index(op.f('fsp_achievements_event_id_idx'), 'fsp_achievements', ['event_id'], unique=False, schema='profile')
    op.create_table('fsp_profiles',
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('fsp_participant_id', sa.String(length=64), nullable=True),
    sa.Column('verification_status', postgresql.ENUM('unlinked', 'pending', 'verified', 'failed', name='fsp_link_status', create_type=False, schema='profile'), server_default=sa.text("'unlinked'"), nullable=False),
    sa.Column('raw_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('linked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('synced_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("verification_status <> 'verified' OR fsp_participant_id IS NOT NULL", name=op.f('fsp_profiles_verified_has_id')),
    sa.ForeignKeyConstraint(['candidate_id'], ['profile.candidates.user_id'], name=op.f('fsp_profiles_candidate_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('candidate_id', name=op.f('fsp_profiles_pkey')),
    sa.UniqueConstraint('fsp_participant_id', name=op.f('fsp_profiles_fsp_participant_id_key')),
    schema='profile'
    )
    op.create_index(op.f('fsp_profiles_verification_status_idx'), 'fsp_profiles', ['verification_status'], unique=False, schema='profile')
    op.create_table('github_accounts',
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('github_user_id', sa.BigInteger(), nullable=False),
    sa.Column('login', sa.String(length=39), nullable=False),
    sa.Column('installation_id', sa.BigInteger(), nullable=True),
    sa.Column('access_token_enc', sa.LargeBinary(), nullable=True),
    sa.Column('scopes', sa.String(length=255), nullable=True),
    sa.Column('linked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('indexed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("login ~ '^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$'", name=op.f('github_accounts_login_format')),
    sa.ForeignKeyConstraint(['candidate_id'], ['profile.candidates.user_id'], name=op.f('github_accounts_candidate_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('candidate_id', name=op.f('github_accounts_pkey')),
    sa.UniqueConstraint('github_user_id', name=op.f('github_accounts_github_user_id_key')),
    sa.UniqueConstraint('login', name=op.f('github_accounts_login_key')),
    schema='profile'
    )
    op.create_table('github_signals',
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('skill_id', sa.BigInteger(), nullable=False),
    sa.Column('score', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('score BETWEEN 0 AND 100', name=op.f('github_signals_score_range')),
    sa.ForeignKeyConstraint(['candidate_id'], ['profile.candidates.user_id'], name=op.f('github_signals_candidate_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['skill_id'], ['ref.skills.id'], name=op.f('github_signals_skill_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('candidate_id', 'skill_id', name=op.f('github_signals_pkey')),
    schema='profile'
    )
    op.create_index(op.f('github_signals_skill_id_idx'), 'github_signals', ['skill_id'], unique=False, schema='profile')
    op.create_table('resume_documents',
    sa.Column('id', sa.UUID(), server_default=sa.text('uuidv7()'), nullable=False),
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('kind', postgresql.ENUM('uploaded', 'generated', name='resume_kind', create_type=False, schema='profile'), nullable=False),
    sa.Column('storage_key', sa.String(length=512), nullable=False),
    sa.Column('file_name', sa.String(length=255), nullable=True),
    sa.Column('mime_type', sa.String(length=100), server_default=sa.text("'application/pdf'"), nullable=False),
    sa.Column('size_bytes', sa.Integer(), nullable=True),
    sa.Column('parse_status', postgresql.ENUM('pending', 'processing', 'done', 'failed', name='parse_status', create_type=False, schema='profile'), server_default=sa.text("'pending'"), nullable=False),
    sa.Column('raw_text', sa.Text(), nullable=True),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('parsed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("kind <> 'generated' OR parse_status = 'done'", name=op.f('resume_documents_generated_is_done')),
    sa.CheckConstraint('char_length(btrim(storage_key)) > 0', name=op.f('resume_documents_key_not_blank')),
    sa.CheckConstraint('char_length(raw_text) <= 200000', name=op.f('resume_documents_raw_text_len')),
    sa.CheckConstraint('size_bytes IS NULL OR (size_bytes > 0 AND size_bytes <= 20971520)', name=op.f('resume_documents_size_range')),
    sa.ForeignKeyConstraint(['candidate_id'], ['profile.candidates.user_id'], name=op.f('resume_documents_candidate_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('resume_documents_pkey')),
    schema='profile'
    )
    op.create_index('resume_documents_candidate_idx', 'resume_documents', ['candidate_id', sa.literal_column('uploaded_at DESC')], unique=False, schema='profile')
    op.create_index('resume_documents_pending_idx', 'resume_documents', ['parse_status'], unique=False, schema='profile', postgresql_where=sa.text("parse_status IN ('pending', 'processing')"))
    op.create_table('regular_assignments',
    sa.Column('id', sa.UUID(), server_default=sa.text('uuidv7()'), nullable=False),
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('task_id', sa.UUID(), nullable=False),
    sa.Column('company_id', sa.UUID(), nullable=True),
    sa.Column('status', postgresql.ENUM('issued', 'viewed', 'answered', 'scored', 'expired', 'skipped', name='assignment_status', create_type=False, schema='assess'), server_default=sa.text("'issued'"), nullable=False),
    sa.Column('answer_text', sa.Text(), nullable=True),
    sa.Column('score', sa.Numeric(precision=6, scale=2), nullable=True),
    sa.Column('max_score', sa.Numeric(precision=6, scale=2), nullable=True),
    sa.Column('issued_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('answered_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('char_length(answer_text) <= 20000', name=op.f('regular_assignments_answer_len')),
    sa.CheckConstraint('due_at IS NULL OR due_at > issued_at', name=op.f('regular_assignments_due_after_issue')),
    sa.CheckConstraint('max_score IS NULL OR max_score > 0', name=op.f('regular_assignments_max_score_positive')),
    sa.CheckConstraint('score IS NULL OR max_score IS NULL OR score <= max_score', name=op.f('regular_assignments_score_within_max')),
    sa.CheckConstraint('score IS NULL OR score >= 0', name=op.f('regular_assignments_score_non_negative')),
    sa.ForeignKeyConstraint(['candidate_id'], ['profile.candidates.user_id'], name=op.f('regular_assignments_candidate_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['company_id'], ['hire.companies.id'], name=op.f('regular_assignments_company_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['task_id'], ['assess.tasks.id'], name=op.f('regular_assignments_task_id_fkey'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('regular_assignments_pkey')),
    schema='assess'
    )
    op.create_index('regular_assignments_candidate_idx', 'regular_assignments', ['candidate_id', sa.literal_column('issued_at DESC')], unique=False, schema='assess')
    op.create_index(op.f('regular_assignments_company_id_idx'), 'regular_assignments', ['company_id'], unique=False, schema='assess')
    op.create_index('regular_assignments_open_idx', 'regular_assignments', ['due_at'], unique=False, schema='assess', postgresql_where=sa.text("status IN ('issued', 'viewed')"))
    op.create_table('survey_answers',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('question_id', sa.BigInteger(), nullable=False),
    sa.Column('option_id', sa.BigInteger(), nullable=True),
    sa.Column('free_text', sa.String(length=1000), nullable=True),
    sa.Column('answered_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('option_id IS NOT NULL OR free_text IS NOT NULL', name=op.f('survey_answers_has_value')),
    sa.ForeignKeyConstraint(['option_id'], ['assess.survey_options.id'], name=op.f('survey_answers_option_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['question_id'], ['assess.survey_questions.id'], name=op.f('survey_answers_question_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['session_id'], ['assess.survey_sessions.id'], name=op.f('survey_answers_session_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('survey_answers_pkey')),
    sa.UniqueConstraint('session_id', 'question_id', 'option_id', name='survey_answers_unique', postgresql_nulls_not_distinct=True),
    schema='assess'
    )
    op.create_index(op.f('survey_answers_session_id_idx'), 'survey_answers', ['session_id'], unique=False, schema='assess')
    op.create_table('test_sessions',
    sa.Column('id', sa.UUID(), server_default=sa.text('uuidv7()'), nullable=False),
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('survey_session_id', sa.UUID(), nullable=True),
    sa.Column('specialization_id', sa.BigInteger(), nullable=False),
    sa.Column('claimed_grade_id', sa.BigInteger(), nullable=False),
    sa.Column('status', postgresql.ENUM('created', 'in_progress', 'submitted', 'scored', 'expired', 'cancelled', name='session_status', create_type=False, schema='assess'), server_default=sa.text("'created'"), nullable=False),
    sa.Column('outcome', postgresql.ENUM('passed', 'failed', 'downgraded', 'upgraded', name='session_outcome', create_type=False, schema='assess'), nullable=True),
    sa.Column('assigned_category_id', sa.BigInteger(), nullable=True),
    sa.Column('total_score', sa.Numeric(precision=7, scale=2), nullable=True),
    sa.Column('max_score', sa.Numeric(precision=7, scale=2), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('deadline_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status <> 'scored' OR (outcome IS NOT NULL AND total_score IS NOT NULL)", name=op.f('test_sessions_scored_has_result')),
    sa.CheckConstraint('finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at', name=op.f('test_sessions_finish_after_start')),
    sa.CheckConstraint('max_score IS NULL OR max_score > 0', name=op.f('test_sessions_max_score_positive')),
    sa.CheckConstraint('total_score IS NULL OR max_score IS NULL OR total_score <= max_score', name=op.f('test_sessions_score_within_max')),
    sa.CheckConstraint('total_score IS NULL OR total_score >= 0', name=op.f('test_sessions_total_score_non_negative')),
    sa.ForeignKeyConstraint(['assigned_category_id'], ['ref.categories.id'], name=op.f('test_sessions_assigned_category_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['candidate_id'], ['profile.candidates.user_id'], name=op.f('test_sessions_candidate_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['claimed_grade_id'], ['ref.grades.id'], name=op.f('test_sessions_claimed_grade_id_fkey'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['specialization_id'], ['ref.specializations.id'], name=op.f('test_sessions_specialization_id_fkey'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['survey_session_id'], ['assess.survey_sessions.id'], name=op.f('test_sessions_survey_session_id_fkey'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('test_sessions_pkey')),
    schema='assess'
    )
    op.create_index('test_sessions_candidate_idx', 'test_sessions', ['candidate_id', sa.literal_column('created_at DESC')], unique=False, schema='assess')
    op.create_index('test_sessions_open_idx', 'test_sessions', ['deadline_at'], unique=False, schema='assess', postgresql_where=sa.text("status IN ('created', 'in_progress')"))
    op.create_table('application_events',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('application_id', sa.UUID(), nullable=False),
    sa.Column('from_status', postgresql.ENUM('sent', 'viewed', 'invited', 'rejected', 'withdrawn', name='application_status', create_type=False, schema='hire'), nullable=True),
    sa.Column('to_status', postgresql.ENUM('sent', 'viewed', 'invited', 'rejected', 'withdrawn', name='application_status', create_type=False, schema='hire'), nullable=False),
    sa.Column('actor_user_id', sa.UUID(), nullable=True),
    sa.Column('comment', sa.String(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('from_status IS DISTINCT FROM to_status', name=op.f('application_events_status_changed')),
    sa.ForeignKeyConstraint(['actor_user_id'], ['core.users.id'], name=op.f('application_events_actor_user_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['application_id'], ['hire.applications.id'], name=op.f('application_events_application_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('application_events_pkey')),
    schema='hire'
    )
    op.create_index('application_events_application_idx', 'application_events', ['application_id', 'created_at'], unique=False, schema='hire')
    op.create_table('invitations',
    sa.Column('id', sa.UUID(), server_default=sa.text('uuidv7()'), nullable=False),
    sa.Column('company_id', sa.UUID(), nullable=False),
    sa.Column('sender_user_id', sa.UUID(), nullable=True),
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('vacancy_id', sa.UUID(), nullable=True),
    sa.Column('need_id', sa.UUID(), nullable=True),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('salary_min', sa.Integer(), nullable=False),
    sa.Column('salary_max', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=3), server_default=sa.text("'RUB'"), nullable=False),
    sa.Column('contact_method', postgresql.ENUM('email', 'phone', 'telegram', 'platform', name='contact_method', create_type=False, schema='hire'), server_default=sa.text("'platform'"), nullable=False),
    sa.Column('contact_value', sa.String(length=120), nullable=True),
    sa.Column('status', postgresql.ENUM('sent', 'viewed', 'accepted', 'declined', 'withdrawn', 'expired', name='invitation_status', create_type=False, schema='hire'), server_default=sa.text("'sent'"), nullable=False),
    sa.Column('decline_reason', sa.String(length=300), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('viewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("contact_method = 'platform' OR char_length(btrim(coalesce(contact_value, ''))) > 0", name=op.f('invitations_contact_value_present')),
    sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f('invitations_currency_format')),
    sa.CheckConstraint("status NOT IN ('accepted', 'declined') OR responded_at IS NOT NULL", name=op.f('invitations_final_has_response')),
    sa.CheckConstraint('char_length(message) BETWEEN 1 AND 5000', name=op.f('invitations_message_len')),
    sa.CheckConstraint('responded_at IS NULL OR responded_at >= sent_at', name=op.f('invitations_responded_after_sent')),
    sa.CheckConstraint('salary_max > 0 AND salary_max <= 100000000', name=op.f('invitations_salary_max_range')),
    sa.CheckConstraint('salary_max >= salary_min', name=op.f('invitations_salary_range')),
    sa.CheckConstraint('salary_min > 0 AND salary_min <= 100000000', name=op.f('invitations_salary_min_range')),
    sa.CheckConstraint('viewed_at IS NULL OR viewed_at >= sent_at', name=op.f('invitations_viewed_after_sent')),
    sa.ForeignKeyConstraint(['candidate_id'], ['profile.candidates.user_id'], name=op.f('invitations_candidate_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['company_id'], ['hire.companies.id'], name=op.f('invitations_company_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['need_id'], ['hire.employer_needs.id'], name=op.f('invitations_need_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['sender_user_id'], ['core.users.id'], name=op.f('invitations_sender_user_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['vacancy_id'], ['hire.vacancies.id'], name=op.f('invitations_vacancy_id_fkey'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('invitations_pkey')),
    schema='hire'
    )
    op.create_index('invitations_candidate_idx', 'invitations', ['candidate_id', 'status', sa.literal_column('sent_at DESC')], unique=False, schema='hire')
    op.create_index('invitations_company_idx', 'invitations', ['company_id', 'status', sa.literal_column('sent_at DESC')], unique=False, schema='hire')
    op.create_index(op.f('invitations_need_id_idx'), 'invitations', ['need_id'], unique=False, schema='hire')
    op.create_index(op.f('invitations_vacancy_id_idx'), 'invitations', ['vacancy_id'], unique=False, schema='hire')
    op.create_table('need_skills',
    sa.Column('need_id', sa.UUID(), nullable=False),
    sa.Column('skill_id', sa.BigInteger(), nullable=False),
    sa.Column('importance', postgresql.ENUM('required', 'nice_to_have', name='skill_importance', create_type=False, schema='hire'), server_default=sa.text("'required'"), nullable=False),
    sa.Column('min_level', sa.Integer(), nullable=True),
    sa.CheckConstraint('min_level IS NULL OR min_level BETWEEN 0 AND 5', name=op.f('need_skills_min_level_range')),
    sa.ForeignKeyConstraint(['need_id'], ['hire.employer_needs.id'], name=op.f('need_skills_need_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['skill_id'], ['ref.skills.id'], name=op.f('need_skills_skill_id_fkey'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('need_id', 'skill_id', name=op.f('need_skills_pkey')),
    schema='hire'
    )
    op.create_index(op.f('need_skills_skill_id_idx'), 'need_skills', ['skill_id'], unique=False, schema='hire')
    op.create_table('chat_threads',
    sa.Column('id', sa.UUID(), server_default=sa.text('uuidv7()'), nullable=False),
    sa.Column('need_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['need_id'], ['hire.employer_needs.id'], name=op.f('chat_threads_need_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['core.users.id'], name=op.f('chat_threads_user_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('chat_threads_pkey')),
    schema='match'
    )
    op.create_index('chat_threads_need_idx', 'chat_threads', ['need_id', sa.literal_column('updated_at DESC')], unique=False, schema='match')
    op.create_table('need_embeddings',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('need_id', sa.UUID(), nullable=False),
    sa.Column('model', sa.String(length=60), nullable=False),
    sa.Column('embedding', pgvector.sqlalchemy.vector.VECTOR(dim=1024), nullable=False),
    sa.Column('source_hash', sa.String(length=64), nullable=False),
    sa.Column('built_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['need_id'], ['hire.employer_needs.id'], name=op.f('need_embeddings_need_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('need_embeddings_pkey')),
    sa.UniqueConstraint('need_id', 'model', name='need_embeddings_uq'),
    schema='match'
    )
    op.create_index('need_embeddings_hnsw_idx', 'need_embeddings', ['embedding'], unique=False, schema='match', postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.create_table('search_runs',
    sa.Column('id', sa.UUID(), server_default=sa.text('uuidv7()'), nullable=False),
    sa.Column('need_id', sa.UUID(), nullable=False),
    sa.Column('author_user_id', sa.UUID(), nullable=True),
    sa.Column('parent_run_id', sa.UUID(), nullable=True),
    sa.Column('strategy', sa.String(length=40), server_default=sa.text("'prefilter_vector_llm'"), nullable=False),
    sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('status', postgresql.ENUM('queued', 'running', 'done', 'failed', name='run_status', create_type=False, schema='match'), server_default=sa.text("'queued'"), nullable=False),
    sa.Column('pool_size', sa.Integer(), nullable=True),
    sa.Column('llm_model', sa.String(length=60), nullable=True),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('error_message', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status <> 'failed' OR error_message IS NOT NULL", name=op.f('search_runs_failed_has_error')),
    sa.CheckConstraint('char_length(summary) <= 10000', name=op.f('search_runs_summary_len')),
    sa.CheckConstraint('parent_run_id IS DISTINCT FROM id', name=op.f('search_runs_no_self_parent')),
    sa.CheckConstraint('pool_size IS NULL OR pool_size >= 0', name=op.f('search_runs_pool_non_negative')),
    sa.ForeignKeyConstraint(['author_user_id'], ['core.users.id'], name=op.f('search_runs_author_user_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['need_id'], ['hire.employer_needs.id'], name=op.f('search_runs_need_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['parent_run_id'], ['match.search_runs.id'], name=op.f('search_runs_parent_run_id_fkey'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('search_runs_pkey')),
    schema='match'
    )
    op.create_index('search_runs_need_idx', 'search_runs', ['need_id', sa.literal_column('created_at DESC')], unique=False, schema='match')
    op.create_index(op.f('search_runs_parent_run_id_idx'), 'search_runs', ['parent_run_id'], unique=False, schema='match')
    op.create_table('github_repos',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('github_repo_id', sa.BigInteger(), nullable=False),
    sa.Column('full_name', sa.String(length=140), nullable=False),
    sa.Column('description', sa.String(length=500), nullable=True),
    sa.Column('is_private', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('is_fork', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('stars', sa.Integer(), server_default='0', nullable=False),
    sa.Column('forks', sa.Integer(), server_default='0', nullable=False),
    sa.Column('commits_by_owner', sa.Integer(), nullable=True),
    sa.Column('primary_language', sa.String(length=40), nullable=True),
    sa.Column('pushed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('indexed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('commits_by_owner IS NULL OR commits_by_owner >= 0', name=op.f('github_repos_commits_non_negative')),
    sa.CheckConstraint('forks >= 0', name=op.f('github_repos_forks_non_negative')),
    sa.CheckConstraint('stars >= 0', name=op.f('github_repos_stars_non_negative')),
    sa.ForeignKeyConstraint(['candidate_id'], ['profile.github_accounts.candidate_id'], name=op.f('github_repos_candidate_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('github_repos_pkey')),
    sa.UniqueConstraint('github_repo_id', name=op.f('github_repos_github_repo_id_key')),
    schema='profile'
    )
    op.create_index(op.f('github_repos_candidate_id_idx'), 'github_repos', ['candidate_id'], unique=False, schema='profile')
    op.create_table('resume_parse_results',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=False),
    sa.Column('model', sa.String(length=60), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('confidence', sa.Numeric(precision=3, scale=2), nullable=True),
    sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('confidence IS NULL OR confidence BETWEEN 0 AND 1', name=op.f('resume_parse_results_confidence_range')),
    sa.ForeignKeyConstraint(['document_id'], ['profile.resume_documents.id'], name=op.f('resume_parse_results_document_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('resume_parse_results_pkey')),
    schema='profile'
    )
    op.create_index(op.f('resume_parse_results_document_id_idx'), 'resume_parse_results', ['document_id'], unique=False, schema='profile')
    op.create_table('grade_assignments',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('category_id', sa.BigInteger(), nullable=False),
    sa.Column('source', postgresql.ENUM('test', 'manual', 'admin', 'import', name='grade_source', create_type=False, schema='assess'), server_default=sa.text("'test'"), nullable=False),
    sa.Column('test_session_id', sa.UUID(), nullable=True),
    sa.Column('assigned_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('next_change_allowed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('is_current', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.CheckConstraint("source <> 'test' OR test_session_id IS NOT NULL", name=op.f('grade_assignments_test_source_has_session')),
    sa.CheckConstraint('next_change_allowed_at >= assigned_at', name=op.f('grade_assignments_cooldown_in_future')),
    sa.ForeignKeyConstraint(['candidate_id'], ['profile.candidates.user_id'], name=op.f('grade_assignments_candidate_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['category_id'], ['ref.categories.id'], name=op.f('grade_assignments_category_id_fkey'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['test_session_id'], ['assess.test_sessions.id'], name=op.f('grade_assignments_test_session_id_fkey'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('grade_assignments_pkey')),
    schema='assess'
    )
    op.create_index('grade_assignments_candidate_idx', 'grade_assignments', ['candidate_id', sa.literal_column('assigned_at DESC')], unique=False, schema='assess')
    op.create_index('grade_assignments_category_idx', 'grade_assignments', ['category_id'], unique=False, schema='assess', postgresql_where=sa.text('is_current'))
    op.create_index('grade_assignments_current_uq', 'grade_assignments', ['candidate_id'], unique=True, schema='assess', postgresql_where=sa.text('is_current'))
    op.create_table('task_answers',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('task_id', sa.UUID(), nullable=False),
    sa.Column('answer_text', sa.Text(), nullable=True),
    sa.Column('answer_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('score', sa.Numeric(precision=6, scale=2), nullable=True),
    sa.Column('max_score', sa.Numeric(precision=6, scale=2), nullable=True),
    sa.Column('checked_by', postgresql.ENUM('auto', 'llm', 'human', name='checked_by', create_type=False, schema='assess'), nullable=True),
    sa.Column('feedback', sa.String(length=2000), nullable=True),
    sa.Column('submitted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('time_spent_sec', sa.Integer(), nullable=True),
    sa.Column('paste_count', sa.SmallInteger(), nullable=True),
    sa.Column('focus_lost_count', sa.SmallInteger(), nullable=True),
    sa.CheckConstraint('char_length(answer_text) <= 50000', name=op.f('task_answers_text_len')),
    sa.CheckConstraint('focus_lost_count IS NULL OR focus_lost_count >= 0', name=op.f('task_answers_focus_non_negative')),
    sa.CheckConstraint('max_score IS NULL OR max_score > 0', name=op.f('task_answers_max_score_positive')),
    sa.CheckConstraint('paste_count IS NULL OR paste_count >= 0', name=op.f('task_answers_paste_non_negative')),
    sa.CheckConstraint('score IS NULL OR max_score IS NULL OR score <= max_score', name=op.f('task_answers_score_within_max')),
    sa.CheckConstraint('score IS NULL OR score >= 0', name=op.f('task_answers_score_non_negative')),
    sa.CheckConstraint('time_spent_sec IS NULL OR time_spent_sec >= 0', name=op.f('task_answers_time_non_negative')),
    sa.ForeignKeyConstraint(['session_id'], ['assess.test_sessions.id'], name=op.f('task_answers_session_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], ['assess.tasks.id'], name=op.f('task_answers_task_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('task_answers_pkey')),
    sa.UniqueConstraint('session_id', 'task_id', name='task_answers_unique'),
    schema='assess'
    )
    op.create_index(op.f('task_answers_session_id_idx'), 'task_answers', ['session_id'], unique=False, schema='assess')
    op.create_index('task_answers_unchecked_idx', 'task_answers', ['submitted_at'], unique=False, schema='assess', postgresql_where=sa.text('checked_at IS NULL'))
    op.create_table('test_session_tasks',
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('task_id', sa.UUID(), nullable=False),
    sa.Column('order_no', sa.SmallInteger(), nullable=False),
    sa.Column('weight', sa.Numeric(precision=4, scale=2), server_default='1.00', nullable=False),
    sa.CheckConstraint('order_no > 0', name=op.f('test_session_tasks_order_positive')),
    sa.CheckConstraint('weight > 0', name=op.f('test_session_tasks_weight_positive')),
    sa.ForeignKeyConstraint(['session_id'], ['assess.test_sessions.id'], name=op.f('test_session_tasks_session_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['task_id'], ['assess.tasks.id'], name=op.f('test_session_tasks_task_id_fkey'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('session_id', 'task_id', name=op.f('test_session_tasks_pkey')),
    sa.UniqueConstraint('session_id', 'order_no', name='test_session_tasks_order_uq'),
    sa.UniqueConstraint('task_id', name='test_session_tasks_task_uq'),
    schema='assess'
    )
    op.create_table('contact_disclosures',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('company_id', sa.UUID(), nullable=False),
    sa.Column('reason', postgresql.ENUM('invitation_accepted', 'application_sent', name='disclosure_reason', create_type=False, schema='hire'), nullable=False),
    sa.Column('invitation_id', sa.UUID(), nullable=True),
    sa.Column('application_id', sa.UUID(), nullable=True),
    sa.Column('granted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(reason = 'invitation_accepted' AND invitation_id IS NOT NULL AND application_id IS NULL) OR (reason = 'application_sent' AND application_id IS NOT NULL AND invitation_id IS NULL)", name=op.f('contact_disclosures_source_matches_reason')),
    sa.CheckConstraint('revoked_at IS NULL OR revoked_at >= granted_at', name=op.f('contact_disclosures_revoked_after_granted')),
    sa.ForeignKeyConstraint(['application_id'], ['hire.applications.id'], name=op.f('contact_disclosures_application_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['candidate_id'], ['profile.candidates.user_id'], name=op.f('contact_disclosures_candidate_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['company_id'], ['hire.companies.id'], name=op.f('contact_disclosures_company_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['invitation_id'], ['hire.invitations.id'], name=op.f('contact_disclosures_invitation_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('contact_disclosures_pkey')),
    schema='hire'
    )
    op.create_index('contact_disclosures_active_uq', 'contact_disclosures', ['candidate_id', 'company_id'], unique=True, schema='hire', postgresql_where=sa.text('revoked_at IS NULL'))
    op.create_index(op.f('contact_disclosures_company_id_idx'), 'contact_disclosures', ['company_id'], unique=False, schema='hire')
    op.create_table('invitation_events',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('invitation_id', sa.UUID(), nullable=False),
    sa.Column('from_status', postgresql.ENUM('sent', 'viewed', 'accepted', 'declined', 'withdrawn', 'expired', name='invitation_status', create_type=False, schema='hire'), nullable=True),
    sa.Column('to_status', postgresql.ENUM('sent', 'viewed', 'accepted', 'declined', 'withdrawn', 'expired', name='invitation_status', create_type=False, schema='hire'), nullable=False),
    sa.Column('actor_user_id', sa.UUID(), nullable=True),
    sa.Column('comment', sa.String(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('from_status IS DISTINCT FROM to_status', name=op.f('invitation_events_status_changed')),
    sa.ForeignKeyConstraint(['actor_user_id'], ['core.users.id'], name=op.f('invitation_events_actor_user_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['invitation_id'], ['hire.invitations.id'], name=op.f('invitation_events_invitation_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('invitation_events_pkey')),
    schema='hire'
    )
    op.create_index('invitation_events_invitation_idx', 'invitation_events', ['invitation_id', 'created_at'], unique=False, schema='hire')
    op.create_table('chat_messages',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('thread_id', sa.UUID(), nullable=False),
    sa.Column('run_id', sa.UUID(), nullable=True),
    sa.Column('role', postgresql.ENUM('user', 'assistant', 'system', 'tool', name='message_role', create_type=False, schema='match'), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('tool_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('tokens', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('char_length(content) BETWEEN 1 AND 20000', name=op.f('chat_messages_content_len')),
    sa.CheckConstraint('tokens IS NULL OR tokens >= 0', name=op.f('chat_messages_tokens_non_negative')),
    sa.ForeignKeyConstraint(['run_id'], ['match.search_runs.id'], name=op.f('chat_messages_run_id_fkey'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['thread_id'], ['match.chat_threads.id'], name=op.f('chat_messages_thread_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('chat_messages_pkey')),
    schema='match'
    )
    op.create_index('chat_messages_thread_idx', 'chat_messages', ['thread_id', 'created_at'], unique=False, schema='match')
    op.create_table('search_results',
    sa.Column('run_id', sa.UUID(), nullable=False),
    sa.Column('candidate_id', sa.UUID(), nullable=False),
    sa.Column('stage', postgresql.ENUM('prefilter', 'vector', 'llm', name='match_stage', create_type=False, schema='match'), nullable=False),
    sa.Column('rank', sa.SmallInteger(), nullable=True),
    sa.Column('vector_score', sa.Numeric(precision=6, scale=5), nullable=True),
    sa.Column('llm_score', sa.Numeric(precision=6, scale=5), nullable=True),
    sa.Column('final_score', sa.Numeric(precision=6, scale=5), nullable=True),
    sa.Column('explanation', sa.String(length=2000), nullable=True),
    sa.Column('is_shown', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("stage <> 'llm' OR explanation IS NOT NULL", name=op.f('search_results_llm_stage_has_explanation')),
    sa.CheckConstraint('final_score IS NULL OR final_score BETWEEN 0 AND 1', name=op.f('search_results_final_score_range')),
    sa.CheckConstraint('llm_score IS NULL OR llm_score BETWEEN 0 AND 1', name=op.f('search_results_llm_score_range')),
    sa.CheckConstraint('rank IS NULL OR rank > 0', name=op.f('search_results_rank_positive')),
    sa.CheckConstraint('vector_score IS NULL OR vector_score BETWEEN -1 AND 1', name=op.f('search_results_vector_score_range')),
    sa.ForeignKeyConstraint(['candidate_id'], ['profile.candidates.user_id'], name=op.f('search_results_candidate_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['run_id'], ['match.search_runs.id'], name=op.f('search_results_run_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('run_id', 'candidate_id', name=op.f('search_results_pkey')),
    sa.UniqueConstraint('run_id', 'rank', name='search_results_rank_uq'),
    schema='match'
    )
    op.create_index(op.f('search_results_candidate_id_idx'), 'search_results', ['candidate_id'], unique=False, schema='match')
    op.create_index('search_results_top_idx', 'search_results', ['run_id', sa.literal_column('final_score DESC')], unique=False, schema='match', postgresql_where=sa.text('is_shown'))
    op.create_table('github_repo_languages',
    sa.Column('repo_id', sa.BigInteger(), nullable=False),
    sa.Column('language', sa.String(length=40), nullable=False),
    sa.Column('bytes', sa.BigInteger(), nullable=False),
    sa.CheckConstraint('bytes > 0', name=op.f('github_repo_languages_bytes_positive')),
    sa.ForeignKeyConstraint(['repo_id'], ['profile.github_repos.id'], name=op.f('github_repo_languages_repo_id_fkey'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('repo_id', 'language', name=op.f('github_repo_languages_pkey')),
    schema='profile'
    )



def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_table('github_repo_languages', schema='profile')
    op.drop_index('search_results_top_idx', table_name='search_results', schema='match', postgresql_where=sa.text('is_shown'))
    op.drop_index(op.f('search_results_candidate_id_idx'), table_name='search_results', schema='match')
    op.drop_table('search_results', schema='match')
    op.drop_index('chat_messages_thread_idx', table_name='chat_messages', schema='match')
    op.drop_table('chat_messages', schema='match')
    op.drop_index('invitation_events_invitation_idx', table_name='invitation_events', schema='hire')
    op.drop_table('invitation_events', schema='hire')
    op.drop_index(op.f('contact_disclosures_company_id_idx'), table_name='contact_disclosures', schema='hire')
    op.drop_index('contact_disclosures_active_uq', table_name='contact_disclosures', schema='hire', postgresql_where=sa.text('revoked_at IS NULL'))
    op.drop_table('contact_disclosures', schema='hire')
    op.drop_table('test_session_tasks', schema='assess')
    op.drop_index('task_answers_unchecked_idx', table_name='task_answers', schema='assess', postgresql_where=sa.text('checked_at IS NULL'))
    op.drop_index(op.f('task_answers_session_id_idx'), table_name='task_answers', schema='assess')
    op.drop_table('task_answers', schema='assess')
    op.drop_index('grade_assignments_current_uq', table_name='grade_assignments', schema='assess', postgresql_where=sa.text('is_current'))
    op.drop_index('grade_assignments_category_idx', table_name='grade_assignments', schema='assess', postgresql_where=sa.text('is_current'))
    op.drop_index('grade_assignments_candidate_idx', table_name='grade_assignments', schema='assess')
    op.drop_table('grade_assignments', schema='assess')
    op.drop_index(op.f('resume_parse_results_document_id_idx'), table_name='resume_parse_results', schema='profile')
    op.drop_table('resume_parse_results', schema='profile')
    op.drop_index(op.f('github_repos_candidate_id_idx'), table_name='github_repos', schema='profile')
    op.drop_table('github_repos', schema='profile')
    op.drop_index(op.f('search_runs_parent_run_id_idx'), table_name='search_runs', schema='match')
    op.drop_index('search_runs_need_idx', table_name='search_runs', schema='match')
    op.drop_table('search_runs', schema='match')
    op.drop_index('need_embeddings_hnsw_idx', table_name='need_embeddings', schema='match', postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_table('need_embeddings', schema='match')
    op.drop_index('chat_threads_need_idx', table_name='chat_threads', schema='match')
    op.drop_table('chat_threads', schema='match')
    op.drop_index(op.f('need_skills_skill_id_idx'), table_name='need_skills', schema='hire')
    op.drop_table('need_skills', schema='hire')
    op.drop_index(op.f('invitations_vacancy_id_idx'), table_name='invitations', schema='hire')
    op.drop_index(op.f('invitations_need_id_idx'), table_name='invitations', schema='hire')
    op.drop_index('invitations_company_idx', table_name='invitations', schema='hire')
    op.drop_index('invitations_candidate_idx', table_name='invitations', schema='hire')
    op.drop_table('invitations', schema='hire')
    op.drop_index('application_events_application_idx', table_name='application_events', schema='hire')
    op.drop_table('application_events', schema='hire')
    op.drop_index('test_sessions_open_idx', table_name='test_sessions', schema='assess', postgresql_where=sa.text("status IN ('created', 'in_progress')"))
    op.drop_index('test_sessions_candidate_idx', table_name='test_sessions', schema='assess')
    op.drop_table('test_sessions', schema='assess')
    op.drop_index(op.f('survey_answers_session_id_idx'), table_name='survey_answers', schema='assess')
    op.drop_table('survey_answers', schema='assess')
    op.drop_index('regular_assignments_open_idx', table_name='regular_assignments', schema='assess', postgresql_where=sa.text("status IN ('issued', 'viewed')"))
    op.drop_index(op.f('regular_assignments_company_id_idx'), table_name='regular_assignments', schema='assess')
    op.drop_index('regular_assignments_candidate_idx', table_name='regular_assignments', schema='assess')
    op.drop_table('regular_assignments', schema='assess')
    op.drop_index('resume_documents_pending_idx', table_name='resume_documents', schema='profile', postgresql_where=sa.text("parse_status IN ('pending', 'processing')"))
    op.drop_index('resume_documents_candidate_idx', table_name='resume_documents', schema='profile')
    op.drop_table('resume_documents', schema='profile')
    op.drop_index(op.f('github_signals_skill_id_idx'), table_name='github_signals', schema='profile')
    op.drop_table('github_signals', schema='profile')
    op.drop_table('github_accounts', schema='profile')
    op.drop_index(op.f('fsp_profiles_verification_status_idx'), table_name='fsp_profiles', schema='profile')
    op.drop_table('fsp_profiles', schema='profile')
    op.drop_index(op.f('fsp_achievements_event_id_idx'), table_name='fsp_achievements', schema='profile')
    op.drop_index(op.f('fsp_achievements_candidate_id_idx'), table_name='fsp_achievements', schema='profile')
    op.drop_table('fsp_achievements', schema='profile')
    op.drop_index('candidate_skills_skill_idx', table_name='candidate_skills', schema='profile')
    op.drop_table('candidate_skills', schema='profile')
    op.drop_table('candidate_privacy', schema='profile')
    op.drop_table('candidate_preferences', schema='profile')
    op.drop_index(op.f('candidate_links_candidate_id_idx'), table_name='candidate_links', schema='profile')
    op.drop_table('candidate_links', schema='profile')
    op.drop_index(op.f('candidate_attribute_values_candidate_id_idx'), table_name='candidate_attribute_values', schema='profile')
    op.drop_index(op.f('candidate_attribute_values_attribute_id_idx'), table_name='candidate_attribute_values', schema='profile')
    op.drop_table('candidate_attribute_values', schema='profile')
    op.drop_index('vacancy_embeddings_hnsw_idx', table_name='vacancy_embeddings', schema='match', postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_table('vacancy_embeddings', schema='match')
    op.drop_index('candidate_embeddings_hnsw_idx', table_name='candidate_embeddings', schema='match', postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.drop_table('candidate_embeddings', schema='match')
    op.drop_index(op.f('vacancy_skills_skill_id_idx'), table_name='vacancy_skills', schema='hire')
    op.drop_table('vacancy_skills', schema='hire')
    op.drop_index('employer_needs_company_idx', table_name='employer_needs', schema='hire', postgresql_where=sa.text('is_active'))
    op.drop_table('employer_needs', schema='hire')
    op.drop_index('applications_vacancy_idx', table_name='applications', schema='hire')
    op.drop_index('applications_candidate_idx', table_name='applications', schema='hire')
    op.drop_table('applications', schema='hire')
    op.drop_index(op.f('tasks_template_id_idx'), table_name='tasks', schema='assess')
    op.drop_index('tasks_candidate_idx', table_name='tasks', schema='assess')
    op.drop_table('tasks', schema='assess')
    op.drop_index('survey_sessions_candidate_idx', table_name='survey_sessions', schema='assess')
    op.drop_table('survey_sessions', schema='assess')
    op.drop_index('candidates_search_idx', table_name='candidates', schema='profile', postgresql_using='gin')
    op.drop_index(op.f('candidates_city_idx'), table_name='candidates', schema='profile')
    op.drop_index('candidates_category_strength_idx', table_name='candidates', schema='profile')
    op.drop_index('candidates_category_idx', table_name='candidates', schema='profile', postgresql_where=sa.text('is_open_to_offers'))
    op.drop_table('candidates', schema='profile')
    op.drop_index('vacancies_search_idx', table_name='vacancies', schema='hire', postgresql_using='gin')
    op.drop_index('vacancies_published_idx', table_name='vacancies', schema='hire', postgresql_where=sa.text("status = 'published'"))
    op.drop_index('vacancies_company_idx', table_name='vacancies', schema='hire')
    op.drop_table('vacancies', schema='hire')
    op.drop_index(op.f('survey_options_question_id_idx'), table_name='survey_options', schema='assess')
    op.drop_table('survey_options', schema='assess')
    op.drop_index(op.f('specialization_skills_skill_id_idx'), table_name='specialization_skills', schema='ref')
    op.drop_table('specialization_skills', schema='ref')
    op.drop_index(op.f('skill_aliases_skill_id_idx'), table_name='skill_aliases', schema='ref')
    op.drop_table('skill_aliases', schema='ref')
    op.drop_index(op.f('categories_grade_id_idx'), table_name='categories', schema='ref')
    op.drop_table('categories', schema='ref')
    op.drop_index(op.f('attribute_options_attribute_id_idx'), table_name='attribute_options', schema='ref')
    op.drop_table('attribute_options', schema='ref')
    op.drop_index(op.f('skill_relevance_skill_id_idx'), table_name='skill_relevance', schema='match')
    op.drop_table('skill_relevance', schema='match')
    op.drop_index(op.f('employers_company_id_idx'), table_name='employers', schema='hire')
    op.drop_table('employers', schema='hire')
    op.drop_index('task_templates_target_idx', table_name='task_templates', schema='assess', postgresql_where=sa.text('is_active'))
    op.drop_index(op.f('task_templates_skill_id_idx'), table_name='task_templates', schema='assess')
    op.drop_table('task_templates', schema='assess')
    op.drop_index(op.f('survey_questions_survey_id_idx'), table_name='survey_questions', schema='assess')
    op.drop_table('survey_questions', schema='assess')
    op.drop_index(op.f('specializations_parent_id_idx'), table_name='specializations', schema='ref')
    op.drop_index(op.f('specializations_industry_id_idx'), table_name='specializations', schema='ref')
    op.drop_table('specializations', schema='ref')
    op.drop_index('skills_name_trgm_idx', table_name='skills', schema='ref', postgresql_using='gin', postgresql_ops={'name': 'gin_trgm_ops'})
    op.drop_index(op.f('skills_kind_idx'), table_name='skills', schema='ref')
    op.drop_index(op.f('skills_area_id_idx'), table_name='skills', schema='ref')
    op.drop_table('skills', schema='ref')
    op.drop_index(op.f('grades_industry_id_idx'), table_name='grades', schema='ref')
    op.drop_table('grades', schema='ref')
    op.drop_index(op.f('attribute_defs_industry_id_idx'), table_name='attribute_defs', schema='ref')
    op.drop_table('attribute_defs', schema='ref')
    op.drop_table('companies', schema='hire')
    op.drop_index('user_roles_role_idx', table_name='user_roles', schema='core')
    op.drop_table('user_roles', schema='core')
    op.drop_index(op.f('user_consents_user_id_idx'), table_name='user_consents', schema='core')
    op.drop_index('user_consents_active_uq', table_name='user_consents', schema='core', postgresql_where=sa.text('revoked_at IS NULL'))
    op.drop_table('user_consents', schema='core')
    op.drop_index('refresh_sessions_user_idx', table_name='refresh_sessions', schema='core', postgresql_where=sa.text('revoked_at IS NULL'))
    op.drop_table('refresh_sessions', schema='core')
    op.drop_index(op.f('email_tokens_user_id_idx'), table_name='email_tokens', schema='core')
    op.drop_index('email_tokens_active_idx', table_name='email_tokens', schema='core', postgresql_where=sa.text('used_at IS NULL'))
    op.drop_table('email_tokens', schema='core')
    op.drop_table('survey_templates', schema='assess')
    op.drop_table('skill_areas', schema='ref')
    op.drop_table('industries', schema='ref')
    op.drop_index('fsp_events_level_idx', table_name='fsp_events', schema='profile')
    op.drop_table('fsp_events', schema='profile')
    op.drop_index('users_status_idx', table_name='users', schema='core')
    op.drop_table('users', schema='core')
    # --- типы и схемы ------------------------------------------------------
    op.execute("DROP TYPE IF EXISTS match.message_role")
    op.execute("DROP TYPE IF EXISTS match.run_status")
    op.execute("DROP TYPE IF EXISTS match.match_stage")
    op.execute("DROP TYPE IF EXISTS hire.disclosure_reason")
    op.execute("DROP TYPE IF EXISTS hire.contact_method")
    op.execute("DROP TYPE IF EXISTS hire.application_status")
    op.execute("DROP TYPE IF EXISTS hire.invitation_status")
    op.execute("DROP TYPE IF EXISTS hire.skill_importance")
    op.execute("DROP TYPE IF EXISTS hire.vacancy_status")
    op.execute("DROP TYPE IF EXISTS hire.company_size")
    op.execute("DROP TYPE IF EXISTS assess.assignment_status")
    op.execute("DROP TYPE IF EXISTS assess.grade_source")
    op.execute("DROP TYPE IF EXISTS assess.checked_by")
    op.execute("DROP TYPE IF EXISTS assess.session_outcome")
    op.execute("DROP TYPE IF EXISTS assess.session_status")
    op.execute("DROP TYPE IF EXISTS assess.template_source")
    op.execute("DROP TYPE IF EXISTS assess.question_format")
    op.execute("DROP TYPE IF EXISTS profile.achievement_source")
    op.execute("DROP TYPE IF EXISTS profile.fsp_event_level")
    op.execute("DROP TYPE IF EXISTS profile.fsp_link_status")
    op.execute("DROP TYPE IF EXISTS profile.parse_status")
    op.execute("DROP TYPE IF EXISTS profile.resume_kind")
    op.execute("DROP TYPE IF EXISTS profile.skill_source")
    op.execute("DROP TYPE IF EXISTS profile.link_kind")
    op.execute("DROP TYPE IF EXISTS profile.employment_type")
    op.execute("DROP TYPE IF EXISTS profile.work_format")
    op.execute("DROP TYPE IF EXISTS ref.attribute_data_type")
    op.execute("DROP TYPE IF EXISTS ref.attribute_scope")
    op.execute("DROP TYPE IF EXISTS ref.skill_kind")
    op.execute("DROP TYPE IF EXISTS core.consent_kind")
    op.execute("DROP TYPE IF EXISTS core.token_purpose")
    op.execute("DROP TYPE IF EXISTS core.user_role")
    op.execute("DROP TYPE IF EXISTS core.user_status")
    op.execute("DROP SCHEMA IF EXISTS match CASCADE")
    op.execute("DROP SCHEMA IF EXISTS hire CASCADE")
    op.execute("DROP SCHEMA IF EXISTS assess CASCADE")
    op.execute("DROP SCHEMA IF EXISTS profile CASCADE")
    op.execute("DROP SCHEMA IF EXISTS ref CASCADE")
    op.execute("DROP SCHEMA IF EXISTS core CASCADE")

