"""profile — профиль кандидата: анкета, резюме, достижения ФСП, сигналы GitHub.

Приватность и предпочтения вынесены в таблицы 1:1 — они читаются разными
сценариями и меняются независимо от самой анкеты.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import enums
from app.db.base import Base

PHONE_RE = r"^\+?[0-9]{7,15}$"
URL_RE = r"^https?://[^[:space:]]{3,}$"

#: Выражение генерируемой колонки полнотекстового поиска. Это именно
#: генерируемая колонка, а не триггер: PostgreSQL пересчитывает её сам.
CANDIDATE_TSVECTOR = (
    "to_tsvector('russian', "
    "coalesce(first_name, '') || ' ' || "
    "coalesce(last_name, '') || ' ' || "
    "coalesce(city, '') || ' ' || "
    "coalesce(about, ''))"
)


class Candidate(Base):
    """Анкета кандидата.

    PK = FK на ``core.users.id``: кандидат — проекция пользователя, отдельный
    суррогат не нужен. Общий ключ физически запрещает двум анкетам указывать
    на одного пользователя.
    """

    __tablename__ = "candidates"
    __table_args__ = (
        CheckConstraint("char_length(btrim(first_name)) > 0", name="first_name_not_blank"),
        CheckConstraint("char_length(btrim(last_name)) > 0", name="last_name_not_blank"),
        # Жёсткие даты, а не now(): CHECK требует иммутабельного выражения
        CheckConstraint(
            "birth_date IS NULL OR birth_date BETWEEN DATE '1930-01-01' AND DATE '2020-01-01'",
            name="birth_date_sane",
        ),
        CheckConstraint(f"phone IS NULL OR phone ~ '{PHONE_RE}'", name="phone_format"),
        # 840 месяцев = 70 лет, верхняя граница здравого смысла
        CheckConstraint("experience_months BETWEEN 0 AND 840", name="experience_range"),
        CheckConstraint("char_length(about) <= 4000", name="about_len"),
        CheckConstraint("profile_strength BETWEEN 0 AND 100", name="strength_range"),
        {"schema": "profile"},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.users.id", ondelete="CASCADE"), primary_key=True
    )
    first_name: Mapped[str] = mapped_column(String(50), nullable=False)
    last_name: Mapped[str] = mapped_column(String(50), nullable=False)
    middle_name: Mapped[str | None] = mapped_column(String(50))
    birth_date: Mapped[date | None] = mapped_column(Date)
    phone: Mapped[str | None] = mapped_column(String(20))
    city: Mapped[str | None] = mapped_column(String(100), index=True)
    relocation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    work_format: Mapped[enums.WorkFormat | None] = mapped_column(enums.work_format)
    employment_type: Mapped[enums.EmploymentType | None] = mapped_column(enums.employment_type)
    experience_months: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    about: Mapped[str | None] = mapped_column(Text)

    #: Денормализация: источник истины — assess.grade_assignments с is_current.
    #: Колонка нужна, чтобы выдача по категории была одним индексным обращением,
    #: а не джойном на историю грейдов при каждом поиске.
    current_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("ref.categories.id", ondelete="SET NULL")
    )
    is_open_to_offers: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    #: «Сила подтверждённого профиля» — ранжирование внутри категории.
    #: Считает бэкенд из результатов тестов, достижений ФСП и свежести профиля.
    profile_strength: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=0, server_default="0"
    )
    #: Когда профиль последний раз подтверждался регулярным заданием
    profile_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed(CANDIDATE_TSVECTOR, persisted=True)
    )


class CandidateLink(Base):
    """Соцсети и отдельная ссылка на сайт-портфолио."""

    __tablename__ = "candidate_links"
    __table_args__ = (
        UniqueConstraint("candidate_id", "kind", "url", name="candidate_links_unique"),
        CheckConstraint(f"url ~ '{URL_RE}'", name="url_format"),
        {"schema": "profile"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile.candidates.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[enums.LinkKind] = mapped_column(enums.link_kind, nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    label: Mapped[str | None] = mapped_column(String(60))
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CandidateSkill(Base):
    """Навык кандидата с двумя уровнями.

    ``self_level`` — что заявил сам кандидат, ``confirmed_level`` — что
    подтвердилось тестом. ТЗ строит всю механику на различии между
    самоописанием и подтверждённым профилем, поэтому схема обязана различать
    это физически, а не договорённостью.
    """

    __tablename__ = "candidate_skills"
    __table_args__ = (
        CheckConstraint("self_level BETWEEN 0 AND 5", name="self_level_range"),
        CheckConstraint(
            "confirmed_level IS NULL OR confirmed_level BETWEEN 0 AND 5", name="confirmed_level_range"
        ),
        CheckConstraint("years IS NULL OR (years >= 0 AND years <= 70)", name="years_range"),
        {"schema": "profile"},
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile.candidates.user_id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[int] = mapped_column(
        ForeignKey("ref.skills.id", ondelete="RESTRICT"), primary_key=True
    )
    self_level: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    #: NULL = не проверялось. Именно это видит работодатель как «подтверждённое»
    confirmed_level: Mapped[int | None] = mapped_column(SmallInteger)
    years: Mapped[Decimal | None] = mapped_column(Numeric(3, 1))
    source: Mapped[enums.SkillSource] = mapped_column(
        enums.skill_source, nullable=False, server_default=text("'self'")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CandidatePreferences(Base):
    """Чего кандидат хочет, включая свободные позитивный и негативный промты.

    Вынесено из анкеты: промты читаются только конвейером подбора — идут в
    эмбеддинг и в контекст LLM, — а при открытии профиля не нужны.
    """

    __tablename__ = "candidate_preferences"
    __table_args__ = (
        CheckConstraint("salary_min IS NULL OR (salary_min > 0 AND salary_min <= 100000000)", name="salary_min_range"),
        CheckConstraint("salary_max IS NULL OR (salary_max > 0 AND salary_max <= 100000000)", name="salary_max_range"),
        CheckConstraint(
            "salary_max IS NULL OR salary_min IS NULL OR salary_max >= salary_min", name="salary_range"
        ),
        CheckConstraint(r"currency ~ '^[A-Z]{3}$'", name="currency_format"),
        {"schema": "profile"},
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile.candidates.user_id", ondelete="CASCADE"), primary_key=True
    )
    desired_specialization_id: Mapped[int | None] = mapped_column(
        ForeignKey("ref.specializations.id", ondelete="SET NULL")
    )
    salary_min: Mapped[int | None] = mapped_column(Integer)
    salary_max: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'RUB'"))
    positive_prompt: Mapped[str | None] = mapped_column(String(1000))
    negative_prompt: Mapped[str | None] = mapped_column(String(1000))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CandidatePrivacy(Base):
    """Что кандидат разрешает показывать работодателю.

    ``show_contacts = true`` не означает, что контакты видно: это согласие
    кандидата в принципе. Фактический доступ даёт hire.contact_disclosures.
    Два независимых условия: кандидат не против — и у компании есть основание.
    """

    __tablename__ = "candidate_privacy"
    __table_args__ = {"schema": "profile"}

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile.candidates.user_id", ondelete="CASCADE"), primary_key=True
    )
    is_searchable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    show_contacts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    show_salary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    show_fsp: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    show_github: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CandidateAttributeValue(Base):
    """Значение EAV-атрибута.

    Три ограничения вместе дают полную типовую гарантию без единого триггера:

    1. составной FK ``(attribute_id, data_type)`` → ``ref.attribute_defs(id, data_type)``
       не даёт подменить тип атрибута;
    2. CHECK по ``data_type`` требует заполнить ровно ту колонку, которая
       этому типу соответствует;
    3. второй CHECK запрещает заполнять больше одной.

    ``UNIQUE NULLS NOT DISTINCT`` нужен, чтобы для не-option атрибута
    (``option_id IS NULL``) второе значение было невозможно: обычный UNIQUE
    считает NULL-ы различными и пропустил бы дубликаты.
    """

    __tablename__ = "candidate_attribute_values"
    __table_args__ = (
        ForeignKeyConstraint(
            ["attribute_id", "data_type"],
            ["ref.attribute_defs.id", "ref.attribute_defs.data_type"],
            ondelete="CASCADE",
            name="cav_attribute_fk",
        ),
        ForeignKeyConstraint(
            ["attribute_id", "option_id"],
            ["ref.attribute_options.attribute_id", "ref.attribute_options.id"],
            ondelete="RESTRICT",
            name="cav_option_fk",
        ),
        CheckConstraint("char_length(value_text) <= 4000", name="cav_text_len"),
        CheckConstraint(
            "(data_type = 'string'  AND value_string  IS NOT NULL)"
            " OR (data_type = 'text'    AND value_text    IS NOT NULL)"
            " OR (data_type = 'integer' AND value_integer IS NOT NULL)"
            " OR (data_type = 'decimal' AND value_decimal IS NOT NULL)"
            " OR (data_type = 'boolean' AND value_boolean IS NOT NULL)"
            " OR (data_type = 'date'    AND value_date    IS NOT NULL)"
            " OR (data_type = 'option'  AND option_id     IS NOT NULL)",
            name="cav_value_matches_type",
        ),
        CheckConstraint(
            "(value_string  IS NOT NULL)::int +"
            " (value_text    IS NOT NULL)::int +"
            " (value_integer IS NOT NULL)::int +"
            " (value_decimal IS NOT NULL)::int +"
            " (value_boolean IS NOT NULL)::int +"
            " (value_date    IS NOT NULL)::int +"
            " (option_id     IS NOT NULL)::int = 1",
            name="cav_exactly_one_value",
        ),
        UniqueConstraint(
            "candidate_id", "attribute_id", "option_id",
            name="cav_unique_value",
            postgresql_nulls_not_distinct=True,
        ),
        {"schema": "profile"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile.candidates.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    attribute_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    data_type: Mapped[enums.AttributeDataType] = mapped_column(enums.attribute_data_type, nullable=False)

    value_string: Mapped[str | None] = mapped_column(String(255))
    value_text: Mapped[str | None] = mapped_column(Text)
    value_integer: Mapped[int | None] = mapped_column(Integer)
    value_decimal: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    value_boolean: Mapped[bool | None] = mapped_column(Boolean)
    value_date: Mapped[date | None] = mapped_column(Date)
    option_id: Mapped[int | None] = mapped_column(BigInteger)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ResumeDocument(Base):
    """И загруженное кандидатом PDF (распознаётся), и сгенерированный
    платформой стандартизированный профиль. ТЗ требует обоих.

    В базе лежит ключ в файловом хранилище, а не сам файл: бинарники в БД
    дороги и неудобны для бэкапов.
    """

    __tablename__ = "resume_documents"
    __table_args__ = (
        CheckConstraint("char_length(btrim(storage_key)) > 0", name="key_not_blank"),
        CheckConstraint("size_bytes IS NULL OR (size_bytes > 0 AND size_bytes <= 20971520)", name="size_range"),
        CheckConstraint("char_length(raw_text) <= 200000", name="raw_text_len"),
        CheckConstraint("kind <> 'generated' OR parse_status = 'done'", name="generated_is_done"),
        {"schema": "profile"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()")
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile.candidates.user_id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[enums.ResumeKind] = mapped_column(enums.resume_kind, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default=text("'application/pdf'")
    )
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    parse_status: Mapped[enums.ParseStatus] = mapped_column(
        enums.parse_status, nullable=False, server_default=text("'pending'")
    )
    #: извлечённый текст: вход для парсинга и для эмбеддинга
    raw_text: Mapped[str | None] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResumeParseResult(Base):
    """Результат разбора резюме моделью.

    Отдельно от документа, чтобы разбор можно было переиграть другой моделью,
    не теряя ни исходник, ни предыдущую версию извлечённых полей.
    ``applied_at IS NULL`` — разобрано, но кандидат ещё не подтвердил перенос
    в анкету: автораспознавание не должно молча переписывать введённое руками.
    """

    __tablename__ = "resume_parse_results"
    __table_args__ = (
        CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="confidence_range"),
        {"schema": "profile"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile.resume_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FspProfile(Base):
    """Связь кандидата с участником реестра ФСП.

    Строка существует у каждого кандидата: отсутствие истории ФСП — это статус
    ``unlinked``, а не отсутствие записи. ТЗ отдельно требует корректной
    обработки такого случая; явный статус делает его нормальным значением, а
    не краевым, на котором кто-нибудь напишет INNER JOIN и потеряет половину
    выдачи.
    """

    __tablename__ = "fsp_profiles"
    __table_args__ = (
        CheckConstraint(
            "verification_status <> 'verified' OR fsp_participant_id IS NOT NULL",
            name="verified_has_id",
        ),
        {"schema": "profile"},
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile.candidates.user_id", ondelete="CASCADE"), primary_key=True
    )
    fsp_participant_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    verification_status: Mapped[enums.FspLinkStatus] = mapped_column(
        enums.fsp_link_status, nullable=False, server_default=text("'unlinked'"), index=True
    )
    #: ответ реестра как есть: структуру данных ФСП нам не дали, разбирать
    #: будем из сохранённого сырья, а не запрашивать заново
    raw_payload: Mapped[dict | None] = mapped_column(JSONB)
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FspEvent(Base):
    """Соревнование или мероприятие ФСП."""

    __tablename__ = "fsp_events"
    __table_args__ = (
        CheckConstraint("char_length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint("ends_on IS NULL OR starts_on IS NULL OR ends_on >= starts_on", name="period"),
        {"schema": "profile"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    discipline: Mapped[str | None] = mapped_column(String(100))
    level: Mapped[enums.FspEventLevel] = mapped_column(enums.fsp_event_level, nullable=False)
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)


class FspAchievement(Base):
    """Результат кандидата в соревновании. Уровень события и место — то, из
    чего бэкенд считает вклад ФСП в ``candidates.profile_strength``."""

    __tablename__ = "fsp_achievements"
    __table_args__ = (
        UniqueConstraint("candidate_id", "event_id", name="fsp_achievements_unique"),
        CheckConstraint("place IS NULL OR (place > 0 AND place <= 10000)", name="place_range"),
        CheckConstraint("participants IS NULL OR participants > 0", name="participants_positive"),
        # третье место из двух не бывает
        CheckConstraint(
            "participants IS NULL OR place IS NULL OR place <= participants",
            name="place_within_participants",
        ),
        {"schema": "profile"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile.candidates.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_id: Mapped[int] = mapped_column(
        ForeignKey("profile.fsp_events.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    place: Mapped[int | None] = mapped_column(SmallInteger)
    participants: Mapped[int | None] = mapped_column(Integer)
    team_name: Mapped[str | None] = mapped_column(String(100))
    score: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    source: Mapped[enums.AchievementSource] = mapped_column(
        enums.achievement_source, nullable=False, server_default=text("'registry'")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict | None] = mapped_column(JSONB)


class GithubAccount(Base):
    """Привязка аккаунта и установки GitHub App для индексации репозиториев."""

    __tablename__ = "github_accounts"
    __table_args__ = (
        # предел длины логина на стороне GitHub — 39 символов
        CheckConstraint(
            r"login ~ '^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$'", name="login_format"
        ),
        {"schema": "profile"},
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile.candidates.user_id", ondelete="CASCADE"), primary_key=True
    )
    github_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    login: Mapped[str] = mapped_column(String(39), nullable=False, unique=True)
    installation_id: Mapped[int | None] = mapped_column(BigInteger)
    #: токен хранится зашифрованным приложением; база видит только шифротекст
    access_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    scopes: Mapped[str | None] = mapped_column(String(255))
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GithubRepo(Base):
    """Проиндексированный репозиторий.

    ``commits_by_owner`` отделяет собственный вклад от форка с чужой историей:
    без него звёздный форк чужого проекта выглядел бы как достижение.
    """

    __tablename__ = "github_repos"
    __table_args__ = (
        CheckConstraint("stars >= 0", name="stars_non_negative"),
        CheckConstraint("forks >= 0", name="forks_non_negative"),
        CheckConstraint("commits_by_owner IS NULL OR commits_by_owner >= 0", name="commits_non_negative"),
        {"schema": "profile"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile.github_accounts.candidate_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    github_repo_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(140), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_fork: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    stars: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    forks: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    commits_by_owner: Mapped[int | None] = mapped_column(Integer)
    primary_language: Mapped[str | None] = mapped_column(String(40))
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GithubRepoLanguage(Base):
    __tablename__ = "github_repo_languages"
    __table_args__ = (
        CheckConstraint("bytes > 0", name="bytes_positive"),
        {"schema": "profile"},
    )

    repo_id: Mapped[int] = mapped_column(
        ForeignKey("profile.github_repos.id", ondelete="CASCADE"), primary_key=True
    )
    language: Mapped[str] = mapped_column(String(40), primary_key=True)
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)


class GithubSignal(Base):
    """Вывод индексации: «по репозиториям видно владение навыком X на уровне Y».

    Отдельная таблица, а не прямая запись в candidate_skills: сигнал
    пересчитывается при каждой переиндексации, а заявленный кандидатом уровень
    при этом трогать нельзя. Бэкенд читает отсюда и обновляет candidate_skills
    со ``source='github'``, оставляя self-строки нетронутыми.
    """

    __tablename__ = "github_signals"
    __table_args__ = (
        CheckConstraint("score BETWEEN 0 AND 100", name="score_range"),
        {"schema": "profile"},
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile.candidates.user_id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[int] = mapped_column(
        ForeignKey("ref.skills.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    evidence: Mapped[dict | None] = mapped_column(JSONB)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# --- индексы -----------------------------------------------------------------
#: Главный путь банка кандидатов
Index(
    "candidates_category_idx",
    Candidate.current_category_id,
    postgresql_where=text("is_open_to_offers"),
)
#: Ранжирование внутри категории по силе подтверждённого профиля
Index(
    "candidates_category_strength_idx",
    Candidate.current_category_id,
    Candidate.profile_strength.desc(),
)
Index("candidates_search_idx", Candidate.search_vector, postgresql_using="gin")

#: Обратный проход: «кто владеет навыком X на уровне не ниже Y»
Index(
    "candidate_skills_skill_idx",
    CandidateSkill.skill_id,
    CandidateSkill.confirmed_level.desc().nullslast(),
)

Index("resume_documents_candidate_idx", ResumeDocument.candidate_id, ResumeDocument.uploaded_at.desc())
#: Очередь на разбор
Index(
    "resume_documents_pending_idx",
    ResumeDocument.parse_status,
    postgresql_where=ResumeDocument.parse_status.in_(["pending", "processing"]),
)
Index("fsp_events_level_idx", FspEvent.level, FspEvent.starts_on.desc())
