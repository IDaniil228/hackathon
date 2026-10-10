"""Справочники предметной области.

Вторая отрасль («Маркетинг») заведена намеренно: она показывает, что схема не
знает про ИТ — специализации, грейды, навыки и банк заданий подставляются
данными, а не кодом.

Все вставки идемпотентны (``ON CONFLICT DO NOTHING`` по естественным ключам),
поэтому команду можно прогонять повторно.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.ref import (
    AttributeDef,
    AttributeOption,
    Category,
    Grade,
    Industry,
    Skill,
    SkillAlias,
    SkillArea,
    Specialization,
    SpecializationSkill,
)

INDUSTRIES = [
    ("it", "Информационные технологии", "Разработка ПО, данные, инфраструктура"),
    ("marketing", "Маркетинг", "Демонстрация доменной независимости платформы"),
]

#: Одна лестница грейдов на отрасль. cooldown_days — «не чаще раза в N дней»
GRADES = [
    ("intern", "Стажёр", 0, 0, 90),
    ("junior", "Junior", 10, 0, 90),
    ("middle", "Middle", 20, 18, 90),
    ("senior", "Senior", 30, 48, 90),
    ("lead", "Lead", 40, 72, 90),
]

SPECIALIZATIONS = {
    "it": [
        ("backend", "Backend-разработка", "Серверная логика, API, хранилища", 10, None),
        ("frontend", "Frontend-разработка", "Веб-интерфейсы", 20, None),
        ("fullstack", "Fullstack-разработка", "Клиент и сервер одним специалистом", 30, None),
        ("mobile", "Мобильная разработка", "iOS, Android, кроссплатформа", 40, None),
        ("ml_engineer", "ML-инженерия", "Модели, инференс, MLOps", 50, None),
        ("data_analyst", "Аналитика данных", "Витрины, метрики, визуализация", 60, None),
        ("devops", "DevOps / SRE", "CI/CD, инфраструктура, надёжность", 70, None),
        ("qa", "Тестирование", "Ручное и автоматизированное тестирование", 80, None),
        (
            "security",
            "Информационная безопасность",
            "AppSec, инфраструктурная безопасность",
            90,
            None,
        ),
        ("systems_analyst", "Системный анализ", "Требования, интеграции, документация", 100, None),
        # подобласти через parent
        ("backend_python", "Backend / Python", None, 11, "backend"),
        ("backend_go", "Backend / Go", None, 12, "backend"),
        ("backend_java", "Backend / Java", None, 13, "backend"),
        ("ml_nlp", "ML / NLP", None, 51, "ml_engineer"),
        ("ml_cv", "ML / CV", None, 52, "ml_engineer"),
    ],
    "marketing": [
        ("smm", "SMM", "Соцсети и сообщества", 10, None),
        ("perf_marketing", "Performance-маркетинг", "Платный трафик и аналитика", 20, None),
    ],
}

SKILL_AREAS = [
    ("web", "Web", 10, None),
    ("ml", "ML", 20, None),
    ("devops", "DevOps", 30, None),
    ("data", "Данные", 40, None),
    ("mobile", "Мобильная разработка", 50, None),
    ("qa", "Тестирование", 60, None),
    ("soft", "SoftSkills", 70, None),
    ("mkt", "Маркетинг", 80, None),
    ("web_backend", "Web / Backend", 11, "web"),
    ("web_frontend", "Web / Frontend", 12, "web"),
    ("ml_nlp_area", "ML / NLP", 21, "ml"),
]

SKILLS = [
    # (область, вид, код, название)
    ("web_backend", "language", "python", "Python"),
    ("web_backend", "language", "go", "Go"),
    ("web_backend", "language", "java", "Java"),
    ("web_backend", "language", "csharp", "C#"),
    ("web_backend", "language", "php", "PHP"),
    ("web_frontend", "language", "typescript", "TypeScript"),
    ("web_frontend", "language", "javascript", "JavaScript"),
    ("mobile", "language", "kotlin", "Kotlin"),
    ("mobile", "language", "swift", "Swift"),
    ("ml", "language", "r_lang", "R"),
    ("data", "language", "sql", "SQL"),
    ("web_backend", "framework", "fastapi", "FastAPI"),
    ("web_backend", "framework", "django", "Django"),
    ("web_backend", "framework", "spring", "Spring"),
    ("web_frontend", "framework", "react", "React"),
    ("web_frontend", "framework", "vue", "Vue"),
    ("ml", "framework", "pytorch", "PyTorch"),
    ("ml", "framework", "tensorflow", "TensorFlow"),
    ("ml_nlp_area", "framework", "transformers", "Transformers"),
    ("mobile", "framework", "flutter", "Flutter"),
    ("web_backend", "tool", "postgresql", "PostgreSQL"),
    ("web_backend", "tool", "redis", "Redis"),
    ("data", "tool", "clickhouse", "ClickHouse"),
    ("data", "tool", "airflow", "Apache Airflow"),
    ("devops", "tool", "docker", "Docker"),
    ("devops", "tool", "kubernetes", "Kubernetes"),
    ("devops", "tool", "gitlab_ci", "GitLab CI"),
    ("devops", "tool", "terraform", "Terraform"),
    ("devops", "tool", "prometheus", "Prometheus"),
    ("qa", "tool", "pytest", "pytest"),
    ("qa", "tool", "playwright", "Playwright"),
    ("ml", "tool", "mlflow", "MLflow"),
    ("ml_nlp_area", "tool", "pgvector_skill", "pgvector"),
    ("web_backend", "practice", "rest_api", "Проектирование REST API"),
    ("web_backend", "practice", "db_design", "Проектирование БД"),
    ("devops", "practice", "ci_cd", "CI/CD"),
    ("qa", "practice", "test_design", "Тест-дизайн"),
    ("ml", "practice", "rag", "RAG"),
    ("ml", "practice", "prompting", "Промт-инжиниринг"),
    # То, что раньше пыталось быть «ролью в команде»: роли пересекаются и не
    # всегда нужны отдельной позицией, а как навык это выражается честнее
    ("soft", "practice", "code_review", "Код-ревью"),
    ("soft", "soft", "communication", "Коммуникация"),
    ("soft", "soft", "teamwork", "Работа в команде"),
    ("soft", "soft", "mentoring", "Менторство"),
    ("soft", "soft", "ownership", "Самостоятельность"),
    ("soft", "soft", "tech_leadership", "Техническое лидерство"),
    ("soft", "soft", "eng_english", "Технический английский"),
    ("mkt", "tool", "yandex_direct", "Яндекс.Директ"),
    ("mkt", "practice", "content_plan", "Контент-планирование"),
]

SKILL_ALIASES = [
    ("python", "питон"),
    ("python", "py"),
    ("postgresql", "Postgres"),
    ("postgresql", "постгрес"),
    ("postgresql", "psql"),
    ("javascript", "JS"),
    ("typescript", "TS"),
    ("kubernetes", "k8s"),
    ("docker", "докер"),
    ("fastapi", "фастапи"),
    ("rest_api", "REST"),
    ("ci_cd", "CICD"),
    ("code_review", "ревью"),
]

#: Только экспертное булево «профильный навык или сопутствующий».
#: Числовой вес считается из наблюдений — см. app.db.relevance.
SPECIALIZATION_SKILLS = [
    ("backend", "python", True),
    ("backend", "postgresql", True),
    ("backend", "rest_api", True),
    ("backend", "db_design", True),
    ("backend", "docker", False),
    ("backend", "redis", False),
    ("backend", "code_review", False),
    ("frontend", "typescript", True),
    ("frontend", "react", True),
    ("frontend", "javascript", False),
    ("ml_engineer", "python", True),
    ("ml_engineer", "pytorch", True),
    ("ml_engineer", "transformers", False),
    ("ml_engineer", "rag", False),
    ("devops", "kubernetes", True),
    ("devops", "docker", True),
    ("devops", "ci_cd", True),
    ("devops", "terraform", False),
    ("qa", "test_design", True),
    ("qa", "pytest", True),
    ("smm", "content_plan", True),
    ("perf_marketing", "yandex_direct", True),
]

ATTRIBUTE_DEFS = [
    # (scope, отрасль|None, код, название, тип, единица, обязательный, публичный, порядок)
    ("candidate", None, "english_level", "Уровень английского", "option", None, False, True, 10),
    ("candidate", None, "available_from", "Готов приступить с", "date", None, False, True, 20),
    (
        "candidate",
        None,
        "business_trips",
        "Готовность к командировкам",
        "boolean",
        None,
        False,
        True,
        30,
    ),
    (
        "candidate",
        None,
        "preferred_team_size",
        "Комфортный размер команды",
        "integer",
        "человек",
        False,
        False,
        40,
    ),
    ("candidate", None, "oss_contributions", "Вклад в open source", "text", None, False, True, 50),
    ("vacancy", None, "has_probation", "Испытательный срок", "boolean", None, False, True, 10),
    # Отраслевой атрибут: существует только у маркетинга и подтверждает, что
    # расширение в другую нишу — это вставка строк, а не ALTER TABLE
    (
        "candidate",
        "marketing",
        "portfolio_cases",
        "Кейсов в портфолио",
        "integer",
        "шт",
        False,
        True,
        60,
    ),
]

ENGLISH_LEVELS = [
    ("a1", "A1 — Beginner", 10),
    ("a2", "A2 — Elementary", 20),
    ("b1", "B1 — Intermediate", 30),
    ("b2", "B2 — Upper-Int.", 40),
    ("c1", "C1 — Advanced", 50),
    ("c2", "C2 — Proficient", 60),
]


async def _ids_by_code(session: AsyncSession, model, code_attr: str = "code") -> dict[str, int]:
    rows = await session.execute(select(getattr(model, code_attr), model.id))
    return {code: pk for code, pk in rows}


async def seed_reference(session: AsyncSession) -> dict[str, int]:
    """Наполнить справочники. Идемпотентно."""
    counts: dict[str, int] = {}

    await session.execute(
        insert(Industry)
        .values([{"code": c, "name": n, "description": d} for c, n, d in INDUSTRIES])
        .on_conflict_do_nothing(index_elements=["code"])
    )
    industries = await _ids_by_code(session, Industry)

    # грейды: одна лестница на каждую отрасль
    await session.execute(
        insert(Grade)
        .values(
            [
                {
                    "industry_id": industries[ind],
                    "code": code,
                    "name": name,
                    "level": level,
                    "min_experience_months": min_exp,
                    "cooldown_days": cooldown,
                }
                for ind in industries
                for code, name, level, min_exp, cooldown in GRADES
            ]
        )
        .on_conflict_do_nothing(constraint="grades_industry_code_uq")
    )

    # специализации: сначала корневые, потом подобласти (нужен parent_id)
    for pass_no in (1, 2):
        rows = []
        existing = {
            (ind, code): pk
            for ind, code, pk in (
                await session.execute(
                    select(Specialization.industry_id, Specialization.code, Specialization.id)
                )
            )
        }
        for ind_code, items in SPECIALIZATIONS.items():
            for code, name, desc, order, parent in items:
                if (pass_no == 1) != (parent is None):
                    continue
                parent_id = existing.get((industries[ind_code], parent)) if parent else None
                rows.append(
                    {
                        "industry_id": industries[ind_code],
                        "parent_id": parent_id,
                        "code": code,
                        "name": name,
                        "description": desc,
                        "sort_order": order,
                    }
                )
        if rows:
            await session.execute(
                insert(Specialization)
                .values(rows)
                .on_conflict_do_nothing(constraint="specializations_industry_code_uq")
            )
        await session.flush()

    # категории: полное декартово произведение специализаций и грейдов отрасли
    specs = (
        await session.execute(
            select(Specialization.id, Specialization.code, Specialization.industry_id)
        )
    ).all()
    grades = (await session.execute(select(Grade.id, Grade.code, Grade.industry_id))).all()
    cat_rows = [
        {"specialization_id": s_id, "grade_id": g_id, "slug": f"{s_code}.{g_code}"}
        for s_id, s_code, s_ind in specs
        for g_id, g_code, g_ind in grades
        if s_ind == g_ind
    ]
    await session.execute(
        insert(Category)
        .values(cat_rows)
        .on_conflict_do_nothing(constraint="categories_spec_grade_uq")
    )
    counts["categories"] = len(cat_rows)

    # области навыков: корневые, затем подобласти
    for pass_no in (1, 2):
        existing = await _ids_by_code(session, SkillArea)
        rows = [
            {
                "code": code,
                "name": name,
                "sort_order": order,
                "parent_id": existing.get(parent) if parent else None,
            }
            for code, name, order, parent in SKILL_AREAS
            if (pass_no == 1) == (parent is None)
        ]
        if rows:
            await session.execute(
                insert(SkillArea).values(rows).on_conflict_do_nothing(index_elements=["code"])
            )
        await session.flush()

    areas = await _ids_by_code(session, SkillArea)
    await session.execute(
        insert(Skill)
        .values(
            [
                {"area_id": areas[area], "kind": kind, "code": code, "name": name}
                for area, kind, code, name in SKILLS
            ]
        )
        .on_conflict_do_nothing(index_elements=["code"])
    )
    skills = await _ids_by_code(session, Skill)
    counts["skills"] = len(skills)

    await session.execute(
        insert(SkillAlias)
        .values([{"skill_id": skills[code], "alias": alias} for code, alias in SKILL_ALIASES])
        .on_conflict_do_nothing(index_elements=["alias"])
    )

    spec_ids = {code: pk for pk, code, _ in specs}
    await session.execute(
        insert(SpecializationSkill)
        .values(
            [
                {"specialization_id": spec_ids[spec], "skill_id": skills[skill], "is_core": is_core}
                for spec, skill, is_core in SPECIALIZATION_SKILLS
            ]
        )
        .on_conflict_do_nothing(index_elements=["specialization_id", "skill_id"])
    )

    await session.execute(
        insert(AttributeDef)
        .values(
            [
                {
                    "scope": scope,
                    "industry_id": industries[ind] if ind else None,
                    "code": code,
                    "name": name,
                    "data_type": dtype,
                    "unit": unit,
                    "is_required": req,
                    "is_public": pub,
                    "sort_order": order,
                }
                for scope, ind, code, name, dtype, unit, req, pub, order in ATTRIBUTE_DEFS
            ]
        )
        .on_conflict_do_nothing(constraint="attribute_defs_scope_code_uq")
    )
    await session.flush()

    english = await session.scalar(
        select(AttributeDef.id).where(
            AttributeDef.scope == "candidate", AttributeDef.code == "english_level"
        )
    )
    await session.execute(
        insert(AttributeOption)
        .values(
            [
                {"attribute_id": english, "code": code, "label": label, "sort_order": order}
                for code, label, order in ENGLISH_LEVELS
            ]
        )
        .on_conflict_do_nothing(constraint="attribute_options_attr_code_uq")
    )

    counts["industries"] = len(industries)
    counts["specializations"] = len(specs)
    return counts
