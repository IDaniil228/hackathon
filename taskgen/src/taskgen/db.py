"""Схема хранения сервиса. Всё живёт в отдельной схеме Postgres taskgen.

Справочники областей, навыков и грейдов — данные, а не перечисления в коде: новая область
добавляется строкой в таблицу, без релиза.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from taskgen.contracts import SessionStatus, TaskStatus

SCHEMA = "taskgen"


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = _now()
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Grade(Base):
    """Ступень грейда. level задаёт только порядок и идёт с шагом 10, чтобы новый грейд
    (например, junior+ с level 15) встал между соседями без перенумерации. Расстояние между
    грейдами в отборе считается по позициям в отсортированном списке, а не по разности level.
    """

    __tablename__ = "grades"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    level: Mapped[int] = mapped_column(SmallInteger, unique=True)
    title: Mapped[str] = mapped_column(String(100))


class Area(Base):
    __tablename__ = "areas"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(100))


class Skill(Base):
    __tablename__ = "skills"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(100))


class AreaSkill(Base):
    """Один навык (например, python) входит в несколько областей."""

    __tablename__ = "area_skills"

    area_code: Mapped[str] = mapped_column(ForeignKey("areas.code"), primary_key=True)
    skill_code: Mapped[str] = mapped_column(ForeignKey("skills.code"), primary_key=True)


class Template(Base):
    """Версия md-шаблона. Экземпляры ссылаются на версию, поэтому старые версии не удаляются."""

    __tablename__ = "templates"
    __table_args__ = (
        UniqueConstraint("name", "version"),
        Index("templates_current_uq", "name", unique=True, postgresql_where=text("is_current")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    area_code: Mapped[str] = mapped_column(ForeignKey("areas.code"))
    grade_code: Mapped[str] = mapped_column(ForeignKey("grades.code"))
    kind: Mapped[str] = mapped_column(String(16))
    runtime: Mapped[str | None] = mapped_column(String(32))
    difficulty: Mapped[int] = mapped_column(SmallInteger)
    skills: Mapped[list[str]] = mapped_column(ARRAY(String(64)))
    description: Mapped[str] = mapped_column(Text)
    # Исходный md целиком — источник правды; поля выше извлечены из него для фильтров.
    body: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _now()


class TemplateEmbedding(Base):
    """Кэш эмбеддинга описания. Ключ (шаблон, модель) и хэш текста делают пересчёт явным.

    Размерность не фиксирована, потому что модель задаётся настройкой. Поэтому индекса нет:
    шаблонов сотни, полный перебор занимает миллисекунды.
    """

    __tablename__ = "template_embeddings"

    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("templates.id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(String(100), primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list[float]] = mapped_column(Vector())


class TestConfig(Base):
    """Настройки теста на категорию; пустое поле берёт значение из SelectionSettings."""

    __tablename__ = "test_configs"
    __test__ = False

    area_code: Mapped[str] = mapped_column(ForeignKey("areas.code"), primary_key=True)
    grade_code: Mapped[str] = mapped_column(ForeignKey("grades.code"), primary_key=True)
    temperature: Mapped[float | None] = mapped_column(Float)
    alpha: Mapped[float | None] = mapped_column(Float)
    beta: Mapped[float | None] = mapped_column(Float)
    code_tasks: Mapped[int | None] = mapped_column(SmallInteger)
    essay_tasks: Mapped[int | None] = mapped_column(SmallInteger)
    quiz_tasks: Mapped[int | None] = mapped_column(SmallInteger)
    min_claimed_per_kind: Mapped[int | None] = mapped_column(SmallInteger)


class TestSession(Base):
    __tablename__ = "test_sessions"
    __test__ = False

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    # Идентификатор кандидата в основном бэкенде; сам по себе персональных данных не несёт.
    candidate_ref: Mapped[str] = mapped_column(String(100), index=True)
    area_code: Mapped[str] = mapped_column(ForeignKey("areas.code"))
    claimed_grade_code: Mapped[str] = mapped_column(ForeignKey("grades.code"))
    skills: Mapped[list[str]] = mapped_column(ARRAY(String(64)))
    temperature: Mapped[float] = mapped_column(Float)
    # Значения и переходы — SessionStatus в taskgen.contracts.
    status: Mapped[str] = mapped_column(String(16), default=SessionStatus.GENERATING)
    # Почему сессия failed; текст уходит клиенту.
    failure: Mapped[str | None] = mapped_column(Text)
    # Кандидат начинает, когда готово первое задание, а не все; правило — в tasks_closed.
    early_start: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # Снимок профиля кандидата (CandidateProfile) — персональные данные. Нужен только
    # генерации и удаляется, когда сгенерировано последнее задание; хэш остаётся, чтобы
    # отметить в profile_changed, что основной бэкенд прислал другой профиль.
    profile: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    profile_hash: Mapped[str | None] = mapped_column(String(64))
    profile_changed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    # Как отбирались шаблоны: режим, параметры, расстояния и вероятности. Пишет генерация при
    # создании сессии; это основа объяснимости выдачи, а result пишет оценка позже.
    selection: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def tasks_closed(self) -> str | None:
        """Почему кандидату не выдаются условия заданий и не принимаются ответы.

        None — условия выдаются. Без раннего старта сессия открыта, только когда готовы все
        задания: иначе сбой позднего задания отнял бы у кандидата уже сделанные ответы. У
        сессии failed оценки не будет, поэтому она закрыта в обоих режимах.
        """
        if self.status == SessionStatus.GENERATING and not self.early_start:
            return "Сессия ещё генерируется: задания выдаются, когда готовы все"
        if self.status == SessionStatus.FAILED:
            return "Генерация сессии не удалась, задания не выдаются; создайте новую сессию"
        return None


class Task(Base):
    """Задание сессии. Строка появляется при создании сессии со статусом pending, а условие
    и скрытые данные получает, когда генерация переводит её в ready."""

    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("session_id", "position"),
        # Оценка и API читают содержимое готового задания без проверок на NULL.
        CheckConstraint(
            "status <> 'ready' OR (statement IS NOT NULL AND params IS NOT NULL "
            "AND public_payload IS NOT NULL AND hidden_payload IS NOT NULL "
            "AND generated_by IS NOT NULL)",
            name="tasks_ready_has_content",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_sessions.id", ondelete="CASCADE"), index=True
    )
    # Шаблон меняется, если прежний не дал экземпляра; прежние — в replacements.
    template_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("templates.id"))
    position: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(String(16), default=TaskStatus.PENDING)
    failure: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))
    statement: Mapped[str | None] = mapped_column(Text)
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    seed: Mapped[int] = mapped_column(BigInteger)
    # То, что видит кандидат: сигнатура, примеры. Скрытая часть кандидату не отдаётся никогда.
    public_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    hidden_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    generated_by: Mapped[str | None] = mapped_column(String(100))
    validated: Mapped[bool] = mapped_column(Boolean, default=False)
    # Что из профиля кандидата повлияло на легенду: {profile, adjacent_skill,
    # adjacent_skill_dropped}; NULL — задание стандартное, например из пула.
    personalization: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Шаблоны, которые стояли в задании раньше: [{template_id, name, reason}].
    replacements: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = _now()


class PoolInstance(Base):
    """Стандартный экземпляр шаблона без персонализации, проверенный эталоном заранее.

    Выдаётся первым заданием одной сессии: строка копируется в tasks и удаляется в той же
    транзакции, поэтому второй раз её не выдать. Своей связи с tasks у пула нет.
    """

    __tablename__ = "pool_instances"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    # Удаление шаблона уносит его запас: выдавать экземпляр без шаблона нельзя.
    template_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("templates.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    statement: Mapped[str] = mapped_column(Text)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    seed: Mapped[int] = mapped_column(BigInteger)
    public_payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    hidden_payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    generated_by: Mapped[str] = mapped_column(String(100))
    validated: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = _now()


class Answer(Base):
    __tablename__ = "answers"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), unique=True
    )
    content: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(32))
    # Поведенческие сигналы фронтенда: вставки, потери фокуса, время.
    behavior: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    test_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    judge: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ai_signal: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    components: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    score: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = _now()


class CandidateSummary(Base):
    """Machine summary. Пишет только сервис; эндпоинта на изменение пользователем нет."""

    __tablename__ = "candidate_summaries"

    candidate_ref: Mapped[str] = mapped_column(String(100), primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# Схема по умолчанию — public, даже если имя пользователя БД совпадает с SCHEMA. Иначе
# рефлексия отдаёт объекты taskgen без схемы, и autogenerate видит ложный дрейф по ключам.
# SQLAlchemy запоминает схему по умолчанию при подключении, поэтому SET позже не помогает.
CONNECT_ARGS = {"server_settings": {"search_path": "public"}}


def make_sessionmaker(database_url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(database_url, pool_pre_ping=True, connect_args=CONNECT_ARGS)
    return async_sessionmaker(engine, expire_on_commit=False)
