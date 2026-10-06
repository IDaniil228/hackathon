"""hire — работодатель, вакансии, потребности, приглашения, отклики.

Смысловой центр продукта — :class:`Invitation`: инициативу проявляет
работодатель, а не кандидат. Поэтому приглашение самодостаточно: оно может
существовать без вакансии, обязано нести зарплатную вилку и способ связи.

Правила видимости из п. 2.2 ТЗ закодированы ограничениями, а не только
логикой хендлеров: вилка NOT NULL и в вакансии, и в приглашении; контакты
кандидата раскрываются только через :class:`ContactDisclosure`; статусы
ведутся в append-only журнале, обе стороны видят одно и то же.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import enums
from app.db.base import Base

EMAIL_RE = r"^[^@[:space:]]+@[^@[:space:]]+\.[A-Za-z]{2,}$"
PHONE_RE = r"^\+?[0-9]{7,15}$"
URL_RE = r"^https?://[^[:space:]]{3,}$"

VACANCY_TSVECTOR = "to_tsvector('russian', coalesce(title, '') || ' ' || coalesce(description, ''))"


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (
        CheckConstraint("char_length(btrim(name)) > 0", name="name_not_blank"),
        # 10 цифр у юрлица, 12 у ИП
        CheckConstraint(r"inn IS NULL OR inn ~ '^[0-9]{10}$' OR inn ~ '^[0-9]{12}$'", name="inn_format"),
        CheckConstraint("char_length(description) <= 5000", name="description_len"),
        CheckConstraint(f"website IS NULL OR website ~ '{URL_RE}'", name="website_format"),
        {"schema": "hire"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()")
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(200))
    inn: Mapped[str | None] = mapped_column(String(12), unique=True)
    industry_id: Mapped[int | None] = mapped_column(ForeignKey("ref.industries.id", ondelete="SET NULL"))
    description: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(String(2048))
    logo_url: Mapped[str | None] = mapped_column(String(2048))
    size: Mapped[enums.CompanySize | None] = mapped_column(enums.company_size)
    city: Mapped[str | None] = mapped_column(String(100))
    #: задел под защиту от недобросовестных работодателей (за рамками MVP)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Employer(Base):
    """Сотрудник компании как пользователь. PK = FK на core.users.id."""

    __tablename__ = "employers"
    __table_args__ = (
        CheckConstraint("char_length(btrim(full_name)) > 0", name="full_name_not_blank"),
        CheckConstraint(f"contact_email IS NULL OR contact_email ~ '{EMAIL_RE}'", name="contact_email_format"),
        CheckConstraint(f"contact_phone IS NULL OR contact_phone ~ '{PHONE_RE}'", name="contact_phone_format"),
        {"schema": "hire"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.users.id", ondelete="CASCADE"), primary_key=True
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hire.companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    position: Mapped[str | None] = mapped_column(String(100))
    contact_email: Mapped[str | None] = mapped_column(CITEXT)
    contact_phone: Mapped[str | None] = mapped_column(String(20))
    is_owner: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Vacancy(Base):
    """Опубликованная вакансия — по ТЗ дополнительный сценарий: основная
    механика работает и без публикации.

    Вилка NOT NULL, а не валидация формы: вакансию без зарплаты нельзя
    создать никаким способом, включая прямой INSERT из консоли.
    """

    __tablename__ = "vacancies"
    __table_args__ = (
        CheckConstraint("char_length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint("char_length(description) BETWEEN 1 AND 20000", name="description_len"),
        CheckConstraint("salary_min > 0 AND salary_min <= 100000000", name="salary_min_range"),
        CheckConstraint("salary_max > 0 AND salary_max <= 100000000", name="salary_max_range"),
        CheckConstraint("salary_max >= salary_min", name="salary_range"),
        CheckConstraint(r"currency ~ '^[A-Z]{3}$'", name="currency_format"),
        CheckConstraint("status <> 'published' OR published_at IS NOT NULL", name="published_has_ts"),
        {"schema": "hire"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hire.companies.id", ondelete="CASCADE"), nullable=False
    )
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("core.users.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("ref.categories.id", ondelete="RESTRICT"), nullable=False
    )
    work_format: Mapped[enums.WorkFormat | None] = mapped_column(enums.work_format)
    employment_type: Mapped[enums.EmploymentType | None] = mapped_column(enums.employment_type)
    city: Mapped[str | None] = mapped_column(String(100))

    #: п. 2.2.1 ТЗ: уровень заработной платы указывается обязательно, диапазоном
    salary_min: Mapped[int] = mapped_column(Integer, nullable=False)
    salary_max: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'RUB'"))

    status: Mapped[enums.VacancyStatus] = mapped_column(
        enums.vacancy_status, nullable=False, server_default=text("'draft'")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed(VACANCY_TSVECTOR, persisted=True)
    )


class VacancySkill(Base):
    __tablename__ = "vacancy_skills"
    __table_args__ = (
        CheckConstraint("min_level IS NULL OR min_level BETWEEN 0 AND 5", name="min_level_range"),
        {"schema": "hire"},
    )

    vacancy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hire.vacancies.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[int] = mapped_column(
        ForeignKey("ref.skills.id", ondelete="RESTRICT"), primary_key=True, index=True
    )
    importance: Mapped[enums.SkillImportance] = mapped_column(
        enums.skill_importance, nullable=False, server_default=text("'required'")
    )
    min_level: Mapped[int | None] = mapped_column(Integer)


class EmployerNeed(Base):
    """Описание потребности — вход конвейера подбора и ключевое отличие от
    доски объявлений.

    Существует отдельно от вакансии: работодатель может вообще ничего не
    публиковать — описал потребность, получил подборку, написал конкретному
    человеку. Именно эту механику ТЗ называет смысловым центром конкурса.
    """

    __tablename__ = "employer_needs"
    __table_args__ = (
        CheckConstraint("char_length(description) BETWEEN 1 AND 20000", name="description_len"),
        CheckConstraint("salary_min IS NULL OR salary_min > 0", name="salary_min_positive"),
        CheckConstraint("salary_max IS NULL OR salary_max > 0", name="salary_max_positive"),
        CheckConstraint(
            "salary_max IS NULL OR salary_min IS NULL OR salary_max >= salary_min", name="salary_range"
        ),
        CheckConstraint(r"currency ~ '^[A-Z]{3}$'", name="currency_format"),
        {"schema": "hire"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hire.companies.id", ondelete="CASCADE"), nullable=False
    )
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("core.users.id", ondelete="SET NULL")
    )
    vacancy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hire.vacancies.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    specialization_id: Mapped[int | None] = mapped_column(
        ForeignKey("ref.specializations.id", ondelete="SET NULL")
    )
    grade_id: Mapped[int | None] = mapped_column(ForeignKey("ref.grades.id", ondelete="SET NULL"))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("ref.categories.id", ondelete="SET NULL"))
    #: свободные предпочтения: идут в эмбеддинг и в системный промт LLM-отбора
    positive_prompt: Mapped[str | None] = mapped_column(String(2000))
    negative_prompt: Mapped[str | None] = mapped_column(String(2000))
    salary_min: Mapped[int | None] = mapped_column(Integer)
    salary_max: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'RUB'"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NeedSkill(Base):
    __tablename__ = "need_skills"
    __table_args__ = (
        CheckConstraint("min_level IS NULL OR min_level BETWEEN 0 AND 5", name="min_level_range"),
        {"schema": "hire"},
    )

    need_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hire.employer_needs.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[int] = mapped_column(
        ForeignKey("ref.skills.id", ondelete="RESTRICT"), primary_key=True, index=True
    )
    importance: Mapped[enums.SkillImportance] = mapped_column(
        enums.skill_importance, nullable=False, server_default=text("'required'")
    )
    min_level: Mapped[int | None] = mapped_column(Integer)


class Invitation(Base):
    """Выход работодателя на контакт с конкретным кандидатом.

    ``vacancy_id`` nullable — прямая цитата ТЗ: приглашение направляется «без
    обязательной привязки к опубликованной вакансии». ``SET NULL``, а не
    ``CASCADE``: приглашение — состоявшийся факт переписки двух людей,
    архивация вакансии не должна стирать его из кабинета кандидата.
    """

    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint("char_length(message) BETWEEN 1 AND 5000", name="message_len"),
        CheckConstraint("salary_min > 0 AND salary_min <= 100000000", name="salary_min_range"),
        CheckConstraint("salary_max > 0 AND salary_max <= 100000000", name="salary_max_range"),
        CheckConstraint("salary_max >= salary_min", name="salary_range"),
        CheckConstraint(r"currency ~ '^[A-Z]{3}$'", name="currency_format"),
        CheckConstraint("viewed_at IS NULL OR viewed_at >= sent_at", name="viewed_after_sent"),
        CheckConstraint("responded_at IS NULL OR responded_at >= sent_at", name="responded_after_sent"),
        CheckConstraint(
            "status NOT IN ('accepted', 'declined') OR responded_at IS NOT NULL",
            name="final_has_response",
        ),
        # выбрать «по телефону» и не указать телефон нельзя
        CheckConstraint(
            "contact_method = 'platform' OR char_length(btrim(coalesce(contact_value, ''))) > 0",
            name="contact_value_present",
        ),
        {"schema": "hire"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()")
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hire.companies.id", ondelete="CASCADE"), nullable=False
    )
    sender_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("core.users.id", ondelete="SET NULL")
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile.candidates.user_id", ondelete="CASCADE"), nullable=False
    )
    vacancy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hire.vacancies.id", ondelete="SET NULL"), index=True
    )
    need_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hire.employer_needs.id", ondelete="SET NULL"), index=True
    )

    message: Mapped[str] = mapped_column(Text, nullable=False)
    #: вилка обязательна: кандидат видит условия до начала общения
    salary_min: Mapped[int] = mapped_column(Integer, nullable=False)
    salary_max: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'RUB'"))
    contact_method: Mapped[enums.ContactMethod] = mapped_column(
        enums.contact_method, nullable=False, server_default=text("'platform'")
    )
    contact_value: Mapped[str | None] = mapped_column(String(120))

    status: Mapped[enums.InvitationStatus] = mapped_column(
        enums.invitation_status, nullable=False, server_default=text("'sent'")
    )
    decline_reason: Mapped[str | None] = mapped_column(String(300))
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InvitationEvent(Base):
    """Журнал смены статусов: обе стороны видят актуальный статус и его путь."""

    __tablename__ = "invitation_events"
    __table_args__ = (
        CheckConstraint("from_status IS DISTINCT FROM to_status", name="status_changed"),
        {"schema": "hire"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    invitation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hire.invitations.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[enums.InvitationStatus | None] = mapped_column(enums.invitation_status)
    to_status: Mapped[enums.InvitationStatus] = mapped_column(enums.invitation_status, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("core.users.id", ondelete="SET NULL")
    )
    comment: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Application(Base):
    """Самостоятельный отклик кандидата — сценарий, в котором кандидат, ещё не
    получивший приглашений, сохраняет возможность проявить инициативу."""

    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("vacancy_id", "candidate_id", name="applications_unique"),
        CheckConstraint("char_length(cover_letter) <= 5000", name="cover_letter_len"),
        {"schema": "hire"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()")
    )
    vacancy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hire.vacancies.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile.candidates.user_id", ondelete="CASCADE"), nullable=False
    )
    cover_letter: Mapped[str | None] = mapped_column(Text)
    status: Mapped[enums.ApplicationStatus] = mapped_column(
        enums.application_status, nullable=False, server_default=text("'sent'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApplicationEvent(Base):
    __tablename__ = "application_events"
    __table_args__ = (
        CheckConstraint("from_status IS DISTINCT FROM to_status", name="status_changed"),
        {"schema": "hire"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hire.applications.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[enums.ApplicationStatus | None] = mapped_column(enums.application_status)
    to_status: Mapped[enums.ApplicationStatus] = mapped_column(enums.application_status, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("core.users.id", ondelete="SET NULL")
    )
    comment: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ContactDisclosure(Base):
    """Кому и на каком основании раскрыты контакты кандидата.

    «Работодатель не видит контактные данные кандидата до момента, когда
    кандидат принял приглашение или откликнулся сам» — здесь это проверяемый
    факт в БД, а не условие в коде: нет строки — бэкенд не отдаёт контакты.

    Вместо полиморфного ``source_id``, по которому нельзя поставить внешний
    ключ, — две типизированные ссылки с CHECK: причина и ссылка обязаны
    совпадать, и заполнена ровно одна из двух.
    """

    __tablename__ = "contact_disclosures"
    __table_args__ = (
        CheckConstraint(
            "(reason = 'invitation_accepted' AND invitation_id IS NOT NULL AND application_id IS NULL)"
            " OR (reason = 'application_sent' AND application_id IS NOT NULL AND invitation_id IS NULL)",
            name="source_matches_reason",
        ),
        CheckConstraint("revoked_at IS NULL OR revoked_at >= granted_at", name="revoked_after_granted"),
        {"schema": "hire"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile.candidates.user_id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hire.companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reason: Mapped[enums.DisclosureReason] = mapped_column(enums.disclosure_reason, nullable=False)
    invitation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hire.invitations.id", ondelete="CASCADE")
    )
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hire.applications.id", ondelete="CASCADE")
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- индексы -----------------------------------------------------------------
Index("vacancies_company_idx", Vacancy.company_id, Vacancy.created_at.desc())
#: Витрина опубликованных вакансий по категориям
Index(
    "vacancies_published_idx",
    Vacancy.category_id,
    Vacancy.published_at.desc(),
    postgresql_where=text("status = 'published'"),
)
Index("vacancies_search_idx", Vacancy.search_vector, postgresql_using="gin")

Index(
    "employer_needs_company_idx",
    EmployerNeed.company_id,
    EmployerNeed.created_at.desc(),
    postgresql_where=text("is_active"),
)

#: Лента входящих приглашений в кабинете кандидата
Index("invitations_candidate_idx", Invitation.candidate_id, Invitation.status, Invitation.sent_at.desc())
#: Отслеживание статусов в кабинете работодателя
Index("invitations_company_idx", Invitation.company_id, Invitation.status, Invitation.sent_at.desc())
Index("invitation_events_invitation_idx", InvitationEvent.invitation_id, InvitationEvent.created_at)

Index("applications_candidate_idx", Application.candidate_id, Application.created_at.desc())
Index("applications_vacancy_idx", Application.vacancy_id, Application.status)
Index("application_events_application_idx", ApplicationEvent.application_id, ApplicationEvent.created_at)

#: Один действующий доступ к контактам на пару «кандидат — компания»
Index(
    "contact_disclosures_active_uq",
    ContactDisclosure.candidate_id,
    ContactDisclosure.company_id,
    unique=True,
    postgresql_where=ContactDisclosure.revoked_at.is_(None),
)
