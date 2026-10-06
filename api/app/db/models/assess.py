"""assess — опрос, банк заданий, тестирование, история грейдов.

Главная проблема механики из ТЗ: единый набор заданий утечёт, а чисто
случайная генерация не даёт сопоставимой сложности. Схема разводит два понятия
и забирает от каждого нужное свойство:

* :class:`TaskTemplate` — калиброванный шаблон (специализация, грейд,
  сложность, рубрика оценивания). Отсюда **сопоставимость** кандидатов.
* :class:`Task` — уникальный экземпляр, сгенерированный под конкретного
  человека. Отсюда **устойчивость к утечке**.

Утёкший экземпляр не обесценивает шаблон, а шаблон целиком кандидату не
показывается никогда.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import enums
from app.db.base import Base


class SurveyTemplate(Base):
    """Входной опрос по отрасли и специализации — первый шаг пути кандидата."""

    __tablename__ = "survey_templates"
    __table_args__ = (
        CheckConstraint("version > 0", name="version_positive"),
        {"schema": "assess"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    industry_id: Mapped[int] = mapped_column(
        ForeignKey("ref.industries.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SurveyQuestion(Base):
    __tablename__ = "survey_questions"
    __table_args__ = (
        UniqueConstraint("survey_id", "order_no", name="survey_questions_order_uq"),
        CheckConstraint("order_no > 0", name="order_positive"),
        CheckConstraint("char_length(btrim(text)) > 0", name="text_not_blank"),
        {"schema": "assess"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    survey_id: Mapped[int] = mapped_column(
        ForeignKey("assess.survey_templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    format: Mapped[enums.QuestionFormat] = mapped_column(enums.question_format, nullable=False)
    text: Mapped[str] = mapped_column(String(500), nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class SurveyOption(Base):
    """Вариант ответа не просто текст — он «ведёт» на специализацию или навык.

    За счёт этого опрос превращается в предварительную классификацию ещё до
    теста, и соответствие «ответ → специализация» остаётся данными, а не кодом.
    """

    __tablename__ = "survey_options"
    __table_args__ = (
        UniqueConstraint("question_id", "order_no", name="survey_options_order_uq"),
        CheckConstraint("order_no > 0", name="order_positive"),
        {"schema": "assess"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("assess.survey_questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    specialization_id: Mapped[int | None] = mapped_column(
        ForeignKey("ref.specializations.id", ondelete="SET NULL")
    )
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("ref.skills.id", ondelete="SET NULL"))
    weight: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), nullable=False, default=1, server_default="1.00"
    )


class SurveySession(Base):
    __tablename__ = "survey_sessions"
    __table_args__ = (
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at", name="finish_after_start"
        ),
        {"schema": "assess"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()")
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile.candidates.user_id", ondelete="CASCADE"), nullable=False
    )
    survey_id: Mapped[int] = mapped_column(
        ForeignKey("assess.survey_templates.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[enums.SessionStatus] = mapped_column(
        enums.session_status, nullable=False, server_default=text("'created'")
    )
    specialization_id: Mapped[int | None] = mapped_column(
        ForeignKey("ref.specializations.id", ondelete="SET NULL")
    )
    claimed_grade_id: Mapped[int | None] = mapped_column(ForeignKey("ref.grades.id", ondelete="SET NULL"))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SurveyAnswer(Base):
    """``UNIQUE NULLS NOT DISTINCT`` покрывает оба формата: для одиночного
    выбора и свободного ответа (``option_id IS NULL``) ответ может быть только
    один, для множественного — по строке на вариант."""

    __tablename__ = "survey_answers"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "question_id", "option_id",
            name="survey_answers_unique",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("option_id IS NOT NULL OR free_text IS NOT NULL", name="has_value"),
        {"schema": "assess"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assess.survey_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("assess.survey_questions.id", ondelete="CASCADE"), nullable=False
    )
    option_id: Mapped[int | None] = mapped_column(ForeignKey("assess.survey_options.id", ondelete="CASCADE"))
    free_text: Mapped[str | None] = mapped_column(String(1000))
    answered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TaskTemplate(Base):
    """Калиброванный шаблон задания — сменная часть системы.

    Пара «промт генератора + рубрика» и есть ответ на вопрос, чем обеспечена
    сопоставимость при разных заданиях: сложность и критерии фиксированы в
    шаблоне, варьируется только формулировка. ``avg_score`` по накопленным
    прохождениям показывает дискриминативность: шаблон, на котором все
    получают максимум, не разделяет сильных и слабых и подлежит замене.
    """

    __tablename__ = "task_templates"
    __table_args__ = (
        CheckConstraint("difficulty BETWEEN 1 AND 10", name="difficulty_range"),
        CheckConstraint(
            "expected_minutes IS NULL OR expected_minutes BETWEEN 1 AND 240", name="minutes_range"
        ),
        CheckConstraint("max_score > 0", name="max_score_positive"),
        CheckConstraint("char_length(generator_prompt) <= 8000", name="gen_prompt_len"),
        CheckConstraint("char_length(grading_rubric) <= 8000", name="rubric_len"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint("usage_count >= 0", name="usage_non_negative"),
        CheckConstraint("avg_score IS NULL OR avg_score >= 0", name="avg_score_non_negative"),
        {"schema": "assess"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    specialization_id: Mapped[int] = mapped_column(
        ForeignKey("ref.specializations.id", ondelete="CASCADE"), nullable=False
    )
    grade_id: Mapped[int] = mapped_column(ForeignKey("ref.grades.id", ondelete="CASCADE"), nullable=False)
    skill_id: Mapped[int | None] = mapped_column(ForeignKey("ref.skills.id", ondelete="SET NULL"), index=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    format: Mapped[enums.QuestionFormat] = mapped_column(enums.question_format, nullable=False)
    difficulty: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    #: ожидаемое время решения: по нему собирается тест заданной длительности
    expected_minutes: Mapped[int | None] = mapped_column(SmallInteger)
    max_score: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, server_default="1")
    #: промт генератора экземпляров
    generator_prompt: Mapped[str | None] = mapped_column(Text)
    #: рубрика проверки с разбалловкой
    grading_rubric: Mapped[str | None] = mapped_column(Text)
    params_schema: Mapped[dict | None] = mapped_column(JSONB)
    source: Mapped[enums.TemplateSource] = mapped_column(
        enums.template_source, nullable=False, server_default=text("'manual'")
    )
    version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1, server_default="1")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    #: накопленная статистика калибровки
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    avg_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Task(Base):
    """Экземпляр задания под конкретного кандидата.

    Одинаковых экземпляров у разных кандидатов не бывает — это и есть защита
    от расхождения заданий по рынку. ``answer_key`` лежит в той же строке, но
    отдавать его клиенту нельзя: выборка полей делается на бэкенде.
    """

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("char_length(statement) BETWEEN 1 AND 20000", name="statement_len"),
        CheckConstraint("difficulty BETWEEN 1 AND 10", name="difficulty_range"),
        CheckConstraint("max_score > 0", name="max_score_positive"),
        {"schema": "assess"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()")
    )
    template_id: Mapped[int] = mapped_column(
        ForeignKey("assess.task_templates.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("profile.candidates.user_id", ondelete="CASCADE")
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    #: эталон и тесты; наружу не отдаётся
    answer_key: Mapped[dict | None] = mapped_column(JSONB)
    difficulty: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    max_score: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    generated_by: Mapped[str | None] = mapped_column(String(60))
    #: сохраняется, чтобы генерацию можно было воспроизвести при разборе спора
    generation_seed: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TestSession(Base):
    """Попытка сдать тест на заявленный грейд.

    Разделение «заявил» (``claimed_grade_id``) и «получил»
    (``assigned_category_id``) — требование ТЗ: грейд не понижается
    принудительно, не сдавший может пройти тест на уровень ниже, а уверенно
    сдавший — подняться.
    """

    __tablename__ = "test_sessions"
    __table_args__ = (
        CheckConstraint(
            "total_score IS NULL OR max_score IS NULL OR total_score <= max_score",
            name="score_within_max",
        ),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="finish_after_start",
        ),
        CheckConstraint(
            "status <> 'scored' OR (outcome IS NOT NULL AND total_score IS NOT NULL)",
            name="scored_has_result",
        ),
        CheckConstraint("total_score IS NULL OR total_score >= 0", name="total_score_non_negative"),
        CheckConstraint("max_score IS NULL OR max_score > 0", name="max_score_positive"),
        {"schema": "assess"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()")
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile.candidates.user_id", ondelete="CASCADE"), nullable=False
    )
    survey_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assess.survey_sessions.id", ondelete="SET NULL")
    )
    specialization_id: Mapped[int] = mapped_column(
        ForeignKey("ref.specializations.id", ondelete="RESTRICT"), nullable=False
    )
    #: грейд, который кандидат заявил сам: тест проверяет именно его
    claimed_grade_id: Mapped[int] = mapped_column(
        ForeignKey("ref.grades.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[enums.SessionStatus] = mapped_column(
        enums.session_status, nullable=False, server_default=text("'created'")
    )
    outcome: Mapped[enums.SessionOutcome | None] = mapped_column(enums.session_outcome)
    #: категория по результату; может отличаться от заявленной
    assigned_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("ref.categories.id", ondelete="SET NULL")
    )
    total_score: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    max_score: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TestSessionTask(Base):
    """Состав теста.

    ``UNIQUE (task_id)`` — один экземпляр задания не может попасть в две
    сессии. Это техническая гарантия того, что задания не переиспользуются
    между кандидатами; нарушить её из кода невозможно.
    """

    __tablename__ = "test_session_tasks"
    __table_args__ = (
        UniqueConstraint("session_id", "order_no", name="test_session_tasks_order_uq"),
        UniqueConstraint("task_id", name="test_session_tasks_task_uq"),
        CheckConstraint("order_no > 0", name="order_positive"),
        CheckConstraint("weight > 0", name="weight_positive"),
        {"schema": "assess"},
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assess.test_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assess.tasks.id", ondelete="RESTRICT"), primary_key=True
    )
    order_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    weight: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False, server_default="1.00")


class TaskAnswer(Base):
    """Ответ на задание с оценкой и сигналами честности прохождения.

    Сигналы в схеме, потому что ТЗ оценивает устойчивость механики к
    распространению заданий: ответ, вставленный из буфера за восемь секунд
    после трёх переключений вкладки, выглядит иначе, чем набранный за двадцать
    минут, — и это должно быть видно в данных, а не только в логах фронтенда.
    """

    __tablename__ = "task_answers"
    __table_args__ = (
        UniqueConstraint("session_id", "task_id", name="task_answers_unique"),
        CheckConstraint("char_length(answer_text) <= 50000", name="text_len"),
        CheckConstraint("score IS NULL OR score >= 0", name="score_non_negative"),
        CheckConstraint("max_score IS NULL OR max_score > 0", name="max_score_positive"),
        CheckConstraint(
            "score IS NULL OR max_score IS NULL OR score <= max_score", name="score_within_max"
        ),
        CheckConstraint("time_spent_sec IS NULL OR time_spent_sec >= 0", name="time_non_negative"),
        CheckConstraint("paste_count IS NULL OR paste_count >= 0", name="paste_non_negative"),
        CheckConstraint("focus_lost_count IS NULL OR focus_lost_count >= 0", name="focus_non_negative"),
        {"schema": "assess"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assess.test_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assess.tasks.id", ondelete="CASCADE"), nullable=False
    )
    answer_text: Mapped[str | None] = mapped_column(Text)
    answer_payload: Mapped[dict | None] = mapped_column(JSONB)
    score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    max_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    checked_by: Mapped[enums.CheckedBy | None] = mapped_column(enums.checked_by)
    feedback: Mapped[str | None] = mapped_column(String(2000))
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    time_spent_sec: Mapped[int | None] = mapped_column(Integer)
    paste_count: Mapped[int | None] = mapped_column(SmallInteger)
    focus_lost_count: Mapped[int | None] = mapped_column(SmallInteger)


class GradeAssignment(Base):
    """История присвоения категорий, append-only.

    ``next_change_allowed_at`` закрывает требование «не чаще одного раза в N
    месяцев»: бэкенд заполняет его из ``ref.grades.cooldown_days`` и сравнивает
    с ``now()`` перед выдачей новой тестовой сессии.
    """

    __tablename__ = "grade_assignments"
    __table_args__ = (
        CheckConstraint("next_change_allowed_at >= assigned_at", name="cooldown_in_future"),
        CheckConstraint(
            "source <> 'test' OR test_session_id IS NOT NULL", name="test_source_has_session"
        ),
        {"schema": "assess"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile.candidates.user_id", ondelete="CASCADE"), nullable=False
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("ref.categories.id", ondelete="RESTRICT"), nullable=False
    )
    source: Mapped[enums.GradeSource] = mapped_column(
        enums.grade_source, nullable=False, server_default=text("'test'")
    )
    test_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assess.test_sessions.id", ondelete="SET NULL")
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    next_change_allowed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    note: Mapped[str | None] = mapped_column(String(300))


class RegularAssignment(Base):
    """Короткая задача «от работодателя»: поддерживает профиль в актуальном
    состоянии (``profile.candidates.profile_refreshed_at``)."""

    __tablename__ = "regular_assignments"
    __table_args__ = (
        CheckConstraint("char_length(answer_text) <= 20000", name="answer_len"),
        CheckConstraint("score IS NULL OR score >= 0", name="score_non_negative"),
        CheckConstraint("max_score IS NULL OR max_score > 0", name="max_score_positive"),
        CheckConstraint(
            "score IS NULL OR max_score IS NULL OR score <= max_score", name="score_within_max"
        ),
        CheckConstraint("due_at IS NULL OR due_at > issued_at", name="due_after_issue"),
        {"schema": "assess"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()")
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile.candidates.user_id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("assess.tasks.id", ondelete="RESTRICT"), nullable=False
    )
    #: от кого задание; NULL — от платформы
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hire.companies.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[enums.AssignmentStatus] = mapped_column(
        enums.assignment_status, nullable=False, server_default=text("'issued'")
    )
    answer_text: Mapped[str | None] = mapped_column(Text)
    score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    max_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --- индексы -----------------------------------------------------------------
Index("survey_sessions_candidate_idx", SurveySession.candidate_id, SurveySession.started_at.desc())

#: Подбор шаблонов под генерацию
Index(
    "task_templates_target_idx",
    TaskTemplate.specialization_id,
    TaskTemplate.grade_id,
    postgresql_where=text("is_active"),
)
Index("tasks_candidate_idx", Task.candidate_id, Task.created_at.desc())
Index("test_sessions_candidate_idx", TestSession.candidate_id, TestSession.created_at.desc())
#: Фоновая задача «просрочить зависшие сессии»
Index(
    "test_sessions_open_idx",
    TestSession.deadline_at,
    postgresql_where=TestSession.status.in_(["created", "in_progress"]),
)
#: Очередь на проверку
Index("task_answers_unchecked_idx", TaskAnswer.submitted_at, postgresql_where=TaskAnswer.checked_at.is_(None))

#: Ровно одна действующая категория на кандидата — партиальным UNIQUE,
#: а не триггером-сторожем
Index(
    "grade_assignments_current_uq",
    GradeAssignment.candidate_id,
    unique=True,
    postgresql_where=text("is_current"),
)
Index("grade_assignments_candidate_idx", GradeAssignment.candidate_id, GradeAssignment.assigned_at.desc())
Index("grade_assignments_category_idx", GradeAssignment.category_id, postgresql_where=text("is_current"))

Index("regular_assignments_candidate_idx", RegularAssignment.candidate_id, RegularAssignment.issued_at.desc())
Index(
    "regular_assignments_open_idx",
    RegularAssignment.due_at,
    postgresql_where=RegularAssignment.status.in_(["issued", "viewed"]),
)
