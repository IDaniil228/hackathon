"""match — векторный поиск, подборки, объяснимость выдачи, чат.

Конвейер подбора: префильтр по категории и жёстким условиям → сортировка по
косинусной близости эмбеддингов → точный отбор LLM с текстовым обоснованием.
Каждая стадия оставляет след в :class:`SearchResult`, поэтому выдачу можно
объяснить работодателю и проверить собственной процедурой валидации.

Модуль Python назван ``matching``, потому что ``match`` — мягкое ключевое
слово Python; имя схемы в PostgreSQL осталось ``match``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
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

#: Размерность под bge-m3. Модель задаёт настройка сервиса подбора
#: ``llm.embedding`` (docs/matcher/generated/settings.md), а не api.
#: Зашита в тип колонки, поэтому смена модели — это миграция, а не настройка.
EMBEDDING_DIM = 1024


class SkillRelevance(Base):
    """Вес навыка в специализации — **вычисляемый**, а не проставленный руками.

    В справочнике ``ref.specialization_skills`` лежит только экспертное булево
    ``is_core``: на вопрос «навык профильный или сопутствующий» человек
    ответить может, а назвать осмысленное число 0.73 — нет. Поэтому вес
    считается из наблюдений и живёт здесь, рядом с подбором, который им
    пользуется.

    Формула — байесовское сглаживание доли упоминаний:

    .. code-block:: text

        weight = (need_mentions + K * prior) / (need_total + K)

        need_mentions — в скольких потребностях и вакансиях этой
                        специализации навык указан как требуемый
        need_total    — сколько всего потребностей и вакансий у специализации
        prior         — 1.0 для is_core, 0.25 для остальных
        K             — 5, вес априорного мнения

    Смысл: пока по специализации мало вакансий, вес тянется к экспертному
    ``is_core`` и система ведёт себя разумно на холодном старте; когда данных
    набралось, их доля перевешивает и вес определяется реальным спросом.
    Никто не вводит числа руками ни в одной точке.

    Пересчёт — :func:`app.db.relevance.recompute_skill_relevance`, по
    расписанию или после заметного изменения набора вакансий.
    """

    __tablename__ = "skill_relevance"
    __table_args__ = (
        CheckConstraint("weight BETWEEN 0 AND 1", name="weight_range"),
        CheckConstraint("need_mentions >= 0", name="need_mentions_non_negative"),
        CheckConstraint("need_total >= need_mentions", name="mentions_within_total"),
        CheckConstraint("candidate_mentions >= 0", name="candidate_mentions_non_negative"),
        {"schema": "match"},
    )

    specialization_id: Mapped[int] = mapped_column(
        ForeignKey("ref.specializations.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[int] = mapped_column(
        ForeignKey("ref.skills.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    #: в скольких потребностях и вакансиях специализации навык указан требуемым
    need_mentions: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: сколько всего потребностей и вакансий у специализации
    need_total: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: у скольких кандидатов специализации навык подтверждён тестом
    candidate_mentions: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    #: итоговый вес 0..1, вход префильтра подбора
    weight: Mapped[Decimal] = mapped_column(Numeric(4, 3), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CandidateEmbedding(Base):
    """Вектор профиля кандидата.

    Таблица на каждую сущность, а не одна полиморфная: полиморфная не даёт
    поставить внешний ключ, а без него осиротевшие вектора накапливаются молча
    и продолжают всплывать в выдаче.

    ``source_hash`` — хеш исходного текста: не пересчитывать вектор, если
    профиль не менялся. Эмбеддинги — самая дорогая часть конвейера.
    """

    __tablename__ = "candidate_embeddings"
    __table_args__ = (
        UniqueConstraint("candidate_id", "model", name="candidate_embeddings_uq"),
        {"schema": "match"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile.candidates.user_id", ondelete="CASCADE"), nullable=False
    )
    model: Mapped[str] = mapped_column(String(60), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    built_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class VacancyEmbedding(Base):
    __tablename__ = "vacancy_embeddings"
    __table_args__ = (
        UniqueConstraint("vacancy_id", "model", name="vacancy_embeddings_uq"),
        {"schema": "match"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    vacancy_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hire.vacancies.id", ondelete="CASCADE"), nullable=False
    )
    model: Mapped[str] = mapped_column(String(60), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    built_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NeedEmbedding(Base):
    __tablename__ = "need_embeddings"
    __table_args__ = (
        UniqueConstraint("need_id", "model", name="need_embeddings_uq"),
        {"schema": "match"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    need_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hire.employer_needs.id", ondelete="CASCADE"), nullable=False
    )
    model: Mapped[str] = mapped_column(String(60), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    built_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SearchRun(Base):
    """Один прогон подбора по потребности.

    Уточнение из чата порождает новый прогон с ``parent_run_id``, а не UPDATE
    существующего: ТЗ требует «фильтрацию и уточнение запроса без потери ранее
    полученной подборки». История подбора остаётся целой, и видно, как
    работодатель сужал запрос.
    """

    __tablename__ = "search_runs"
    __table_args__ = (
        CheckConstraint("parent_run_id IS DISTINCT FROM id", name="no_self_parent"),
        CheckConstraint("status <> 'failed' OR error_message IS NOT NULL", name="failed_has_error"),
        CheckConstraint("pool_size IS NULL OR pool_size >= 0", name="pool_non_negative"),
        CheckConstraint("char_length(summary) <= 10000", name="summary_len"),
        {"schema": "match"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()")
    )
    need_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hire.employer_needs.id", ondelete="CASCADE"), nullable=False
    )
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("core.users.id", ondelete="SET NULL")
    )
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("match.search_runs.id", ondelete="SET NULL"), index=True
    )
    strategy: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default=text("'prefilter_vector_llm'")
    )
    #: фактические пороги, размер пула, доля случайных резюме — без них
    #: выдачу нельзя ни воспроизвести, ни объяснить через неделю
    params: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[enums.RunStatus] = mapped_column(
        enums.run_status, nullable=False, server_default=text("'queued'")
    )
    pool_size: Mapped[int | None] = mapped_column(Integer)
    llm_model: Mapped[str | None] = mapped_column(String(60))
    #: обзор подборки от LLM: то, что работодатель обсуждает в чате
    summary: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SearchResult(Base):
    """Кандидат в прогоне с оценками и объяснением.

    Три отдельных балла, а не один итоговый: ТЗ требует обоснованности
    ранжирования, и при разборе нужно видеть, векторная близость подвела или
    LLM. ``stage`` показывает, где кандидат отсеялся, — это материал для
    собственной процедуры валидации.
    """

    __tablename__ = "search_results"
    __table_args__ = (
        UniqueConstraint("run_id", "rank", name="search_results_rank_uq"),
        CheckConstraint("rank IS NULL OR rank > 0", name="rank_positive"),
        CheckConstraint(
            "vector_score IS NULL OR vector_score BETWEEN -1 AND 1", name="vector_score_range"
        ),
        CheckConstraint("llm_score IS NULL OR llm_score BETWEEN 0 AND 1", name="llm_score_range"),
        CheckConstraint(
            "final_score IS NULL OR final_score BETWEEN 0 AND 1", name="final_score_range"
        ),
        # дошёл до LLM — обязан иметь текстовое обоснование
        CheckConstraint(
            "stage <> 'llm' OR explanation IS NOT NULL", name="llm_stage_has_explanation"
        ),
        {"schema": "match"},
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("match.search_runs.id", ondelete="CASCADE"), primary_key=True
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile.candidates.user_id", ondelete="CASCADE"), primary_key=True, index=True
    )
    #: до какой стадии конвейера дошёл кандидат
    stage: Mapped[enums.MatchStage] = mapped_column(enums.match_stage, nullable=False)
    rank: Mapped[int | None] = mapped_column(SmallInteger)
    vector_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    llm_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    final_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    #: краткая объяснимость выдачи — требование п. 2.1 ТЗ
    explanation: Mapped[str | None] = mapped_column(String(2000))
    #: разложение по детерминированным факторам: из него пишется ``explanation``
    #: и по нему фильтруется готовый прогон. Состав факторов задаёт сервис
    #: подбора, см. docs/matcher/matching-mechanics.md
    factors: Mapped[dict | None] = mapped_column(JSONB)
    is_shown: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ChatThread(Base):
    __tablename__ = "chat_threads"
    __table_args__ = {"schema": "match"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuidv7()")
    )
    need_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hire.employer_needs.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("core.users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ChatMessage(Base):
    """Реплика в чате над подборкой.

    ``run_id`` связывает реплику с версией выдачи: видно, какое сообщение к
    какой подборке привело, и можно вернуться к любой.
    """

    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint("char_length(content) BETWEEN 1 AND 20000", name="content_len"),
        CheckConstraint("tokens IS NULL OR tokens >= 0", name="tokens_non_negative"),
        {"schema": "match"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("match.chat_threads.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("match.search_runs.id", ondelete="SET NULL")
    )
    role: Mapped[enums.MessageRole] = mapped_column(enums.message_role, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_payload: Mapped[dict | None] = mapped_column(JSONB)
    tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# --- индексы -----------------------------------------------------------------
#: HNSW по косинусу: быстрый ANN-поиск ближайших резюме к описанию потребности.
#: Работает, только если вектор для сравнения — константа, параметр или
#: скалярный подзапрос; вектор из JOIN уводит планировщик в Seq Scan.
Index(
    "candidate_embeddings_hnsw_idx",
    CandidateEmbedding.embedding,
    postgresql_using="hnsw",
    postgresql_ops={"embedding": "vector_cosine_ops"},
)
Index(
    "vacancy_embeddings_hnsw_idx",
    VacancyEmbedding.embedding,
    postgresql_using="hnsw",
    postgresql_ops={"embedding": "vector_cosine_ops"},
)
Index(
    "need_embeddings_hnsw_idx",
    NeedEmbedding.embedding,
    postgresql_using="hnsw",
    postgresql_ops={"embedding": "vector_cosine_ops"},
)

Index("search_runs_need_idx", SearchRun.need_id, SearchRun.created_at.desc())
Index(
    "search_results_top_idx",
    SearchResult.run_id,
    SearchResult.final_score.desc(),
    postgresql_where=text("is_shown"),
)
Index("chat_threads_need_idx", ChatThread.need_id, ChatThread.updated_at.desc())
Index("chat_messages_thread_idx", ChatMessage.thread_id, ChatMessage.created_at)
