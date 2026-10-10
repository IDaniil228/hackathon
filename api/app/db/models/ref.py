"""ref — справочники предметной области и описание EAV.

Вся таксономия (отрасль → специализация → грейд → категория, область навыков →
навык) лежит в данных, а не в коде и не в ENUM-типах. Это то, что позволяет
загрузить банк заданий другой области и использовать ту же систему вне ИТ:
достаточно вставить строки, а не менять схему.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.db import enums
from app.db.base import Base

CODE_RE = r"^[a-z][a-z0-9_]{1,39}$"


class Industry(Base):
    """Отрасль. Точка расширения за пределы ИТ: новая отрасль — это строка плюс
    свой банк заданий, без единого ALTER TABLE."""

    __tablename__ = "industries"
    __table_args__ = (
        CheckConstraint(f"code ~ '{CODE_RE}'", name="code_format"),
        CheckConstraint("char_length(btrim(name)) > 0", name="name_not_blank"),
        {"schema": "ref"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Specialization(Base):
    """Специализация внутри отрасли. ``parent_id`` даёт подобласти:
    backend → backend_python, ml_engineer → ml_nlp."""

    __tablename__ = "specializations"
    __table_args__ = (
        UniqueConstraint("industry_id", "code", name="specializations_industry_code_uq"),
        CheckConstraint(f"code ~ '{CODE_RE}'", name="code_format"),
        CheckConstraint("char_length(btrim(name)) > 0", name="name_not_blank"),
        # IS DISTINCT FROM, а не <>: с NULL сравнение дало бы NULL и CHECK пропустил бы строку
        CheckConstraint("parent_id IS DISTINCT FROM id", name="no_self_parent"),
        {"schema": "ref"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    industry_id: Mapped[int] = mapped_column(
        ForeignKey("ref.industries.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("ref.specializations.id", ondelete="RESTRICT"), index=True
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    sort_order: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class Grade(Base):
    """Лестница грейдов внутри отрасли.

    ``cooldown_days`` живёт здесь, а не в коде: ТЗ оставляет интервал смены
    грейда на усмотрение команды, и менять его правкой строки дешевле, чем
    релизом. Заодно разные ступени могут иметь разный кулдаун — подняться со
    стажёра логично разрешать чаще, чем с сеньора.
    """

    __tablename__ = "grades"
    __table_args__ = (
        UniqueConstraint("industry_id", "code", name="grades_industry_code_uq"),
        UniqueConstraint("industry_id", "level", name="grades_industry_level_uq"),
        CheckConstraint(f"code ~ '{CODE_RE}'", name="code_format"),
        CheckConstraint("char_length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("level BETWEEN 0 AND 100", name="level_range"),
        CheckConstraint(
            "min_experience_months IS NULL OR min_experience_months BETWEEN 0 AND 840",
            name="min_experience_range",
        ),
        CheckConstraint("cooldown_days BETWEEN 0 AND 3650", name="cooldown_range"),
        {"schema": "ref"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    industry_id: Mapped[int] = mapped_column(
        ForeignKey("ref.industries.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    #: порядковый уровень: по нему сравниваются «грейд ниже / выше»
    level: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    min_experience_months: Mapped[int | None] = mapped_column(SmallInteger)
    #: минимальный интервал между сменами грейда
    cooldown_days: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=90, server_default="90"
    )


class Category(Base):
    """Категория из ТЗ — связка специализации и грейда.

    Сделана отдельной сущностью, а не парой колонок в каждой таблице: иначе на
    неё нельзя сослаться одним внешним ключом, а ссылаются четверо — кандидат,
    вакансия, результат теста и выдача подбора.
    """

    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("specialization_id", "grade_id", name="categories_spec_grade_uq"),
        CheckConstraint(r"slug ~ '^[a-z][a-z0-9_.-]{1,89}$'", name="slug_format"),
        {"schema": "ref"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    specialization_id: Mapped[int] = mapped_column(
        ForeignKey("ref.specializations.id", ondelete="RESTRICT"), nullable=False
    )
    grade_id: Mapped[int] = mapped_column(
        ForeignKey("ref.grades.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(90), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class SkillArea(Base):
    """Области навыков (ML, Web, DevOps, SoftSkills) и подобласти через parent_id."""

    __tablename__ = "skill_areas"
    __table_args__ = (
        CheckConstraint(f"code ~ '{CODE_RE}'", name="code_format"),
        CheckConstraint("char_length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("parent_id IS DISTINCT FROM id", name="no_self_parent"),
        {"schema": "ref"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("ref.skill_areas.id", ondelete="RESTRICT")
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    sort_order: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )


class Skill(Base):
    """Навык. ``kind`` разделяет язык, фреймворк, инструмент, практику и
    soft-skill — в интерфейсе это разные поля внутри одной области."""

    __tablename__ = "skills"
    __table_args__ = (
        CheckConstraint(r"code ~ '^[a-z][a-z0-9_+.-]{1,49}$'", name="code_format"),
        CheckConstraint("char_length(btrim(name)) > 0", name="name_not_blank"),
        {"schema": "ref"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    area_id: Mapped[int] = mapped_column(
        ForeignKey("ref.skill_areas.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    kind: Mapped[enums.SkillKind] = mapped_column(enums.skill_kind, nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class SkillAlias(Base):
    """Синонимы для разбора резюме и ответов LLM: «Постгрес», «psql»,
    «PostgreSQL» → один skill_id. citext снимает нормализацию с бэкенда."""

    __tablename__ = "skill_aliases"
    __table_args__ = (
        CheckConstraint("char_length(alias) BETWEEN 1 AND 60", name="len"),
        {"schema": "ref"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    skill_id: Mapped[int] = mapped_column(
        ForeignKey("ref.skills.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alias: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)


class SpecializationSkill(Base):
    """Какие навыки относятся к специализации.

    Здесь только экспертное булево ``is_core`` — «навык профильный для этой
    специализации или сопутствующий». Числового веса в справочнике нет
    намеренно: человек не может осмысленно проставить 0.73 против 0.68, такие
    цифры берутся с потолка и создают ложную точность.

    Числовой вес считается из наблюдений и живёт в :class:`match.SkillRelevance`.
    """

    __tablename__ = "specialization_skills"
    __table_args__ = {"schema": "ref"}

    specialization_id: Mapped[int] = mapped_column(
        ForeignKey("ref.specializations.id", ondelete="CASCADE"), primary_key=True
    )
    skill_id: Mapped[int] = mapped_column(
        ForeignKey("ref.skills.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    #: профильный навык специализации, а не сопутствующий
    is_core: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


class AttributeDef(Base):
    """Описание поля, которого нет в фиксированных колонках.

    Фиксированные колонки покрывают то, что одинаково для любой отрасли (ФИО,
    стаж, формат работы); всё отраслевое описывается здесь. Без этого каждая
    новая ниша требовала бы ALTER TABLE, а с голым jsonb исчезли бы и схема,
    и любая проверка.
    """

    __tablename__ = "attribute_defs"
    __table_args__ = (
        UniqueConstraint("scope", "code", name="attribute_defs_scope_code_uq"),
        # Пара (id, data_type) — мишень составного FK из таблиц значений.
        # Благодаря ей тип значения нельзя разойтись с объявлением атрибута.
        UniqueConstraint("id", "data_type", name="attribute_defs_id_type_uq"),
        CheckConstraint(r"code ~ '^[a-z][a-z0-9_]{1,49}$'", name="code_format"),
        CheckConstraint("char_length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("NOT is_multi OR data_type = 'option'", name="multi_only_for_option"),
        {"schema": "ref"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    scope: Mapped[enums.AttributeScope] = mapped_column(enums.attribute_scope, nullable=False)
    #: NULL = атрибут общий для всех отраслей
    industry_id: Mapped[int | None] = mapped_column(
        ForeignKey("ref.industries.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    data_type: Mapped[enums.AttributeDataType] = mapped_column(
        enums.attribute_data_type, nullable=False
    )
    unit: Mapped[str | None] = mapped_column(String(20))
    is_multi: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    is_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    #: видно ли работодателю по умолчанию
    is_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    sort_order: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )


class AttributeOption(Base):
    """Вариант значения для атрибута типа ``option``."""

    __tablename__ = "attribute_options"
    __table_args__ = (
        UniqueConstraint("attribute_id", "code", name="attribute_options_attr_code_uq"),
        # Избыточный на вид UNIQUE: он позволяет таблице значений сослаться
        # составным ключом (attribute_id, option_id) и гарантировать, что
        # выбранный вариант принадлежит именно этому атрибуту.
        UniqueConstraint("attribute_id", "id", name="attribute_options_attr_id_uq"),
        CheckConstraint(r"code ~ '^[a-z][a-z0-9_]{1,49}$'", name="code_format"),
        CheckConstraint("char_length(btrim(label)) > 0", name="label_not_blank"),
        {"schema": "ref"},
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    attribute_id: Mapped[int] = mapped_column(
        ForeignKey("ref.attribute_defs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )


# --- индексы -----------------------------------------------------------------
#: Автодополнение по подстроке в названии навыка
Index(
    "skills_name_trgm_idx",
    Skill.name,
    postgresql_using="gin",
    postgresql_ops={"name": "gin_trgm_ops"},
)
