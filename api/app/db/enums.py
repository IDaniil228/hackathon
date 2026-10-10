"""Перечисления предметной области.

Каждому Python-enum соответствует нативный ENUM-тип PostgreSQL в схеме своего
модуля: ``hire.invitation_status``, ``assess.session_status`` и так далее.

Граница между ENUM-типом и таблицей-справочником проходит по вопросу «кто
добавляет новое значение». Новый статус приглашения неизбежно означает новую
ветку в коде — пусть база такое значение и не пропустит без миграции. Новая
специализация кода не касается вообще, поэтому она строка в ``ref``.

``create_type=False`` у всех типов намеренно: типы создаёт миграция одним
явным проходом по :data:`ALL_ENUMS`, иначе SQLAlchemy пытался бы создать один
и тот же тип при каждой использующей его таблице.
"""

import enum

from sqlalchemy.dialects.postgresql import ENUM

from app.db.base import metadata


class StrEnum(enum.StrEnum):
    """Значение в БД совпадает со значением в Python."""

    def __str__(self) -> str:
        return self.value


def _pg(py_enum: type[StrEnum], name: str, schema: str) -> ENUM:
    return ENUM(
        py_enum,
        name=name,
        schema=schema,
        metadata=metadata,
        create_type=False,
        values_callable=lambda e: [member.value for member in e],
    )


# --- core --------------------------------------------------------------------
class UserStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    BLOCKED = "blocked"


class UserRole(StrEnum):
    CANDIDATE = "candidate"
    EMPLOYER = "employer"
    ADMIN = "admin"


class TokenPurpose(StrEnum):
    VERIFY_EMAIL = "verify_email"
    RESET_PASSWORD = "reset_password"


class ConsentKind(StrEnum):
    PD_PROCESSING = "pd_processing"
    PROFILE_PUBLICATION = "profile_publication"
    FSP_DATA_SYNC = "fsp_data_sync"


# --- ref ---------------------------------------------------------------------
class SkillKind(StrEnum):
    LANGUAGE = "language"
    FRAMEWORK = "framework"
    TOOL = "tool"
    PRACTICE = "practice"
    SOFT = "soft"
    DOMAIN = "domain"


class AttributeScope(StrEnum):
    CANDIDATE = "candidate"
    VACANCY = "vacancy"
    COMPANY = "company"
    TASK = "task"


class AttributeDataType(StrEnum):
    STRING = "string"
    TEXT = "text"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATE = "date"
    OPTION = "option"


# --- profile -----------------------------------------------------------------
class WorkFormat(StrEnum):
    OFFICE = "office"
    HYBRID = "hybrid"
    REMOTE = "remote"


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    PROJECT = "project"
    INTERNSHIP = "internship"
    CONTRACT = "contract"


class LinkKind(StrEnum):
    GITHUB = "github"
    GITLAB = "gitlab"
    TELEGRAM = "telegram"
    PORTFOLIO = "portfolio"
    LINKEDIN = "linkedin"
    HABR = "habr"
    OTHER = "other"


class SkillSource(StrEnum):
    SELF = "self"
    RESUME = "resume"
    TEST = "test"
    GITHUB = "github"
    FSP = "fsp"


class ResumeKind(StrEnum):
    UPLOADED = "uploaded"
    GENERATED = "generated"


class ParseStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class FspLinkStatus(StrEnum):
    UNLINKED = "unlinked"
    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"


class FspEventLevel(StrEnum):
    LOCAL = "local"
    REGIONAL = "regional"
    FEDERAL = "federal"
    INTERNATIONAL = "international"


class AchievementSource(StrEnum):
    REGISTRY = "registry"
    MANUAL = "manual"


# --- assess ------------------------------------------------------------------
class QuestionFormat(StrEnum):
    SINGLE_CHOICE = "single_choice"
    MULTI_CHOICE = "multi_choice"
    OPEN = "open"
    CODE = "code"
    CASE = "case"
    SCALE = "scale"


class TemplateSource(StrEnum):
    MANUAL = "manual"
    LLM = "llm"
    IMPORTED = "imported"


class SessionStatus(StrEnum):
    CREATED = "created"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    SCORED = "scored"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class SessionOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    DOWNGRADED = "downgraded"
    UPGRADED = "upgraded"


class CheckedBy(StrEnum):
    AUTO = "auto"
    LLM = "llm"
    HUMAN = "human"


class GradeSource(StrEnum):
    TEST = "test"
    MANUAL = "manual"
    ADMIN = "admin"
    IMPORT = "import"


class AssignmentStatus(StrEnum):
    ISSUED = "issued"
    VIEWED = "viewed"
    ANSWERED = "answered"
    SCORED = "scored"
    EXPIRED = "expired"
    SKIPPED = "skipped"


# --- hire --------------------------------------------------------------------
class CompanySize(StrEnum):
    MICRO = "micro"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    ENTERPRISE = "enterprise"


class VacancyStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    PAUSED = "paused"
    ARCHIVED = "archived"


class SkillImportance(StrEnum):
    REQUIRED = "required"
    NICE_TO_HAVE = "nice_to_have"


class InvitationStatus(StrEnum):
    SENT = "sent"
    VIEWED = "viewed"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    WITHDRAWN = "withdrawn"
    EXPIRED = "expired"


class ApplicationStatus(StrEnum):
    SENT = "sent"
    VIEWED = "viewed"
    INVITED = "invited"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


class ContactMethod(StrEnum):
    EMAIL = "email"
    PHONE = "phone"
    TELEGRAM = "telegram"
    PLATFORM = "platform"


class DisclosureReason(StrEnum):
    INVITATION_ACCEPTED = "invitation_accepted"
    APPLICATION_SENT = "application_sent"


# --- match -------------------------------------------------------------------
class MatchStage(StrEnum):
    PREFILTER = "prefilter"
    VECTOR = "vector"
    LLM = "llm"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


# --- типы PostgreSQL ---------------------------------------------------------
user_status = _pg(UserStatus, "user_status", "core")
user_role = _pg(UserRole, "user_role", "core")
token_purpose = _pg(TokenPurpose, "token_purpose", "core")
consent_kind = _pg(ConsentKind, "consent_kind", "core")

skill_kind = _pg(SkillKind, "skill_kind", "ref")
attribute_scope = _pg(AttributeScope, "attribute_scope", "ref")
attribute_data_type = _pg(AttributeDataType, "attribute_data_type", "ref")

work_format = _pg(WorkFormat, "work_format", "profile")
employment_type = _pg(EmploymentType, "employment_type", "profile")
link_kind = _pg(LinkKind, "link_kind", "profile")
skill_source = _pg(SkillSource, "skill_source", "profile")
resume_kind = _pg(ResumeKind, "resume_kind", "profile")
parse_status = _pg(ParseStatus, "parse_status", "profile")
fsp_link_status = _pg(FspLinkStatus, "fsp_link_status", "profile")
fsp_event_level = _pg(FspEventLevel, "fsp_event_level", "profile")
achievement_source = _pg(AchievementSource, "achievement_source", "profile")

question_format = _pg(QuestionFormat, "question_format", "assess")
template_source = _pg(TemplateSource, "template_source", "assess")
session_status = _pg(SessionStatus, "session_status", "assess")
session_outcome = _pg(SessionOutcome, "session_outcome", "assess")
checked_by = _pg(CheckedBy, "checked_by", "assess")
grade_source = _pg(GradeSource, "grade_source", "assess")
assignment_status = _pg(AssignmentStatus, "assignment_status", "assess")

company_size = _pg(CompanySize, "company_size", "hire")
vacancy_status = _pg(VacancyStatus, "vacancy_status", "hire")
skill_importance = _pg(SkillImportance, "skill_importance", "hire")
invitation_status = _pg(InvitationStatus, "invitation_status", "hire")
application_status = _pg(ApplicationStatus, "application_status", "hire")
contact_method = _pg(ContactMethod, "contact_method", "hire")
disclosure_reason = _pg(DisclosureReason, "disclosure_reason", "hire")

match_stage = _pg(MatchStage, "match_stage", "match")
run_status = _pg(RunStatus, "run_status", "match")
message_role = _pg(MessageRole, "message_role", "match")

#: Полный список типов в порядке создания. Миграция проходит по нему явно.
ALL_ENUMS: tuple[ENUM, ...] = (
    user_status,
    user_role,
    token_purpose,
    consent_kind,
    skill_kind,
    attribute_scope,
    attribute_data_type,
    work_format,
    employment_type,
    link_kind,
    skill_source,
    resume_kind,
    parse_status,
    fsp_link_status,
    fsp_event_level,
    achievement_source,
    question_format,
    template_source,
    session_status,
    session_outcome,
    checked_by,
    grade_source,
    assignment_status,
    company_size,
    vacancy_status,
    skill_importance,
    invitation_status,
    application_status,
    contact_method,
    disclosure_reason,
    match_stage,
    run_status,
    message_role,
)
