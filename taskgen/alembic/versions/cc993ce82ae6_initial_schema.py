"""Начальная схема и справочники областей, навыков и грейдов

Revision ID: cc993ce82ae6
Revises:
Create Date: 2026-10-06 18:51:54.655854
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "cc993ce82ae6"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Шаг 10 оставляет место для промежуточных грейдов вроде junior+ (level 15).
GRADES = [("junior", 10, "Junior"), ("middle", 20, "Middle"), ("senior", 30, "Senior")]
AREAS = [("ml", "Машинное обучение"), ("algo", "Алгоритмы"), ("backend", "Бэкенд")]
SKILLS = {
    "python": ("Python", ["ml", "algo", "backend"]),
    "pytorch": ("PyTorch", ["ml"]),
    "scikit-learn": ("scikit-learn", ["ml"]),
    "numpy": ("NumPy", ["ml"]),
    "cpp": ("C++", ["algo"]),
    "sorting": ("Сортировки", ["algo"]),
    "greedy": ("Жадные алгоритмы", ["algo"]),
    "two-pointers": ("Два указателя", ["algo"]),
    "graphs": ("Графы", ["algo"]),
    "dynamic-programming": ("Динамическое программирование", ["algo"]),
    "django": ("Django", ["backend"]),
    "docker": ("Docker", ["backend"]),
    "sql": ("SQL", ["backend"]),
}


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "api_keys",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash"),
        schema="taskgen",
    )
    op.create_table(
        "areas",
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("code"),
        schema="taskgen",
    )
    op.create_table(
        "candidate_summaries",
        sa.Column("candidate_ref", sa.String(length=100), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("candidate_ref"),
        schema="taskgen",
    )
    op.create_table(
        "grades",
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("level", sa.SmallInteger(), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("code"),
        sa.UniqueConstraint("level"),
        schema="taskgen",
    )
    op.create_table(
        "skills",
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("code"),
        schema="taskgen",
    )
    op.create_table(
        "area_skills",
        sa.Column("area_code", sa.String(length=32), nullable=False),
        sa.Column("skill_code", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["area_code"],
            ["taskgen.areas.code"],
        ),
        sa.ForeignKeyConstraint(
            ["skill_code"],
            ["taskgen.skills.code"],
        ),
        sa.PrimaryKeyConstraint("area_code", "skill_code"),
        schema="taskgen",
    )
    op.create_table(
        "templates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("area_code", sa.String(length=32), nullable=False),
        sa.Column("grade_code", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("runtime", sa.String(length=32), nullable=True),
        sa.Column("difficulty", sa.SmallInteger(), nullable=False),
        sa.Column("skills", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["area_code"],
            ["taskgen.areas.code"],
        ),
        sa.ForeignKeyConstraint(
            ["grade_code"],
            ["taskgen.grades.code"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version"),
        schema="taskgen",
    )
    op.create_index(
        "templates_current_uq",
        "templates",
        ["name"],
        unique=True,
        schema="taskgen",
        postgresql_where=sa.text("is_current"),
    )
    op.create_table(
        "test_configs",
        sa.Column("area_code", sa.String(length=32), nullable=False),
        sa.Column("grade_code", sa.String(length=32), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("alpha", sa.Float(), nullable=True),
        sa.Column("beta", sa.Float(), nullable=True),
        sa.Column("tasks_per_test", sa.SmallInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["area_code"],
            ["taskgen.areas.code"],
        ),
        sa.ForeignKeyConstraint(
            ["grade_code"],
            ["taskgen.grades.code"],
        ),
        sa.PrimaryKeyConstraint("area_code", "grade_code"),
        schema="taskgen",
    )
    op.create_table(
        "test_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("candidate_ref", sa.String(length=100), nullable=False),
        sa.Column("area_code", sa.String(length=32), nullable=False),
        sa.Column("claimed_grade_code", sa.String(length=32), nullable=False),
        sa.Column("skills", postgresql.ARRAY(sa.String(length=64)), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["area_code"],
            ["taskgen.areas.code"],
        ),
        sa.ForeignKeyConstraint(
            ["claimed_grade_code"],
            ["taskgen.grades.code"],
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="taskgen",
    )
    op.create_index(
        op.f("ix_taskgen_test_sessions_candidate_ref"),
        "test_sessions",
        ["candidate_ref"],
        unique=False,
        schema="taskgen",
    )
    op.create_table(
        "tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.UUID(), nullable=False),
        sa.Column("template_id", sa.UUID(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("public_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("hidden_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("generated_by", sa.String(length=100), nullable=False),
        sa.Column("validated", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["session_id"], ["taskgen.test_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["taskgen.templates.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="taskgen",
    )
    op.create_index(
        op.f("ix_taskgen_tasks_session_id"), "tasks", ["session_id"], unique=False, schema="taskgen"
    )
    op.create_table(
        "template_embeddings",
        sa.Column("template_id", sa.UUID(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["taskgen.templates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("template_id", "model"),
        schema="taskgen",
    )
    op.create_table(
        "answers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=True),
        sa.Column("behavior", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("test_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("analysis", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("judge", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ai_signal", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("components", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["task_id"], ["taskgen.tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
        schema="taskgen",
    )
    _seed()


def _seed() -> None:
    meta = sa.MetaData(schema="taskgen")
    grades = sa.Table("grades", meta, autoload_with=op.get_bind())
    areas = sa.Table("areas", meta, autoload_with=op.get_bind())
    skills = sa.Table("skills", meta, autoload_with=op.get_bind())
    area_skills = sa.Table("area_skills", meta, autoload_with=op.get_bind())
    op.bulk_insert(grades, [{"code": c, "level": lv, "title": t} for c, lv, t in GRADES])
    op.bulk_insert(areas, [{"code": c, "title": t} for c, t in AREAS])
    op.bulk_insert(skills, [{"code": c, "title": t} for c, (t, _) in SKILLS.items()])
    op.bulk_insert(
        area_skills,
        [{"area_code": a, "skill_code": c} for c, (_, ar) in SKILLS.items() for a in ar],
    )


def downgrade() -> None:
    op.drop_table("answers", schema="taskgen")
    op.drop_table("template_embeddings", schema="taskgen")
    op.drop_index(op.f("ix_taskgen_tasks_session_id"), table_name="tasks", schema="taskgen")
    op.drop_table("tasks", schema="taskgen")
    op.drop_index(
        op.f("ix_taskgen_test_sessions_candidate_ref"), table_name="test_sessions", schema="taskgen"
    )
    op.drop_table("test_sessions", schema="taskgen")
    op.drop_table("test_configs", schema="taskgen")
    op.drop_index(
        "templates_current_uq",
        table_name="templates",
        schema="taskgen",
        postgresql_where=sa.text("is_current"),
    )
    op.drop_table("templates", schema="taskgen")
    op.drop_table("area_skills", schema="taskgen")
    op.drop_table("skills", schema="taskgen")
    op.drop_table("grades", schema="taskgen")
    op.drop_table("candidate_summaries", schema="taskgen")
    op.drop_table("areas", schema="taskgen")
    op.drop_table("api_keys", schema="taskgen")
