from __future__ import annotations

import os
import random
import uuid
from datetime import UTC, datetime, timedelta

from faker import Faker
from sqlalchemy import select, text

from app.db.models import assess, core, hire, profile
from app.db.models.ref import Category, Industry, Skill, Specialization
from app.db.seeds.demo import entry_survey
from app.security import hasher

NAMESPACE = uuid.UUID("9a47ebaf-ef12-449b-9acd-49714fa321ac")
ARCHETYPES = {
    "backend": [
        (
            "платёжные сервисы",
            ["python", "postgresql", "db_design", "fastapi"],
            "транзакции и идемпотентные API",
            "финтех",
        ),
        (
            "высоконагруженные API",
            ["go", "redis", "docker", "rest_api"],
            "профилирование, кеши и балансировку нагрузки",
            "маркетплейс",
        ),
        (
            "корпоративные сервисы",
            ["java", "postgresql", "sql"],
            "интеграции и миграции данных",
            "логистику",
        ),
        (
            "сервисы событий",
            ["python", "redis", "docker"],
            "очереди, повторную доставку и обработку событий",
            "телеком",
        ),
    ],
    "frontend": [
        (
            "продуктовые интерфейсы",
            ["typescript", "react"],
            "доступность, формы и дизайн-системы",
            "e-commerce",
        ),
        (
            "аналитические панели",
            ["typescript", "react", "rest_api"],
            "визуализацию данных и производительность интерфейса",
            "аналитику",
        ),
        (
            "веб-приложения",
            ["javascript", "vue"],
            "адаптивную вёрстку и взаимодействие с API",
            "образование",
        ),
    ],
    "ml_engineer": [
        (
            "системы поиска",
            ["python", "pytorch", "rag", "transformers"],
            "эмбеддинги, переранжирование и оценку nDCG",
            "поиск",
        ),
        (
            "компьютерное зрение",
            ["python", "pytorch"],
            "детекцию объектов и оптимизацию инференса",
            "промышленность",
        ),
        (
            "рекомендательные системы",
            ["python", "mlflow", "pytorch"],
            "эксперименты, ранжирование и A/B-тесты",
            "маркетплейс",
        ),
    ],
    "devops": [
        (
            "инфраструктуру сервисов",
            ["kubernetes", "terraform", "docker"],
            "наблюдаемость, надёжность и автоматизацию релизов",
            "облачные сервисы",
        ),
        (
            "платформу разработки",
            ["docker", "python", "kubernetes"],
            "CI/CD, окружения и восстановление после инцидентов",
            "продуктовую разработку",
        ),
    ],
    "qa": [
        (
            "автоматизацию тестирования",
            ["python", "pytest", "test_design"],
            "контрактные тесты, фикстуры и проверку API",
            "финтех",
        ),
        (
            "качество веб-приложений",
            ["typescript", "test_design"],
            "e2e, доступность и регрессионные проверки",
            "e-commerce",
        ),
    ],
    "fullstack": [
        (
            "продукт целиком",
            ["python", "fastapi", "react", "postgresql"],
            "интерфейсы, API и модель данных",
            "B2B",
        ),
        (
            "веб-платформу",
            ["typescript", "react", "docker"],
            "клиентскую и серверную логику",
            "образование",
        ),
    ],
}


def make_profiles(count: int, seed: int, as_of: datetime) -> list[dict]:
    fake = Faker("ru_RU")
    fake.seed_instance(seed)
    rng = random.Random(seed)
    result = []
    for n in range(count):
        specialization = list(ARCHETYPES)[n % len(ARCHETYPES)]
        subject, skills, work, domain = rng.choice(ARCHETYPES[specialization])
        grade = rng.choices(["junior", "middle", "senior"], [2, 6, 2])[0]
        experience = rng.randint(
            *{"junior": (3, 20), "middle": (24, 65), "senior": (66, 150)}[grade]
        )
        low = {"junior": 90000, "middle": 180000, "senior": 300000}[grade] + rng.randrange(
            0, 80000, 10000
        )
        sector = rng.choice(
            [
                "командой продукта",
                "небольшой командой",
                "распределённой командой",
                "платформенной командой",
            ]
        )
        result.append(
            {
                "id": uuid.uuid5(NAMESPACE, f"{seed}:candidate:{n}"),
                "email": "candidate@scepka.example"
                if n == 0
                else f"candidate-{seed}-{n}@scepka.example",
                "first_name": fake.first_name(),
                "last_name": fake.last_name(),
                "city": rng.choice(
                    [
                        "Москва",
                        "Санкт-Петербург",
                        "Казань",
                        "Екатеринбург",
                        "Новосибирск",
                        "Пермь",
                        "Самара",
                        "Уфа",
                    ]
                ),
                "specialization": specialization,
                "grade": grade,
                "experience_months": experience,
                "salary_min": low,
                "salary_max": low + rng.randrange(40000, 110000, 10000),
                "about": f"Разрабатываю {subject} для отрасли {domain}. Отвечаю за {work}. "
                f"Работаю с {sector}. Опыт {experience} месяцев. "
                f"Сейчас развиваю {rng.choice(skills)} и делюсь опытом на код-ревью.",
                "skills": skills,
                "level": {"junior": 2, "middle": 3, "senior": 5}[grade],
                "created_at": as_of - timedelta(days=rng.randint(1, 180)),
            }
        )
    return result


async def seed_synthetic(session, *, count: int, seed: int, as_of: datetime | None):
    if os.environ.get("SCEPKA_DEMO_ENABLED") != "true":
        raise ValueError("Для синтетического наполнения задайте SCEPKA_DEMO_ENABLED=true")
    password = os.environ.get("SCEPKA_DEMO_PASSWORD", "")
    if len(password) < 12:
        raise ValueError("SCEPKA_DEMO_PASSWORD должен содержать не меньше 12 символов")
    as_of = as_of or datetime.now(UTC)
    if as_of.tzinfo is None:
        raise ValueError("--as-of должен содержать часовой пояс")
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": f"scepka-demo:{seed}"},
    )
    categories = {row.slug: row for row in (await session.scalars(select(Category))).all()}
    specs = {row.code: row.id for row in (await session.scalars(select(Specialization))).all()}
    skills = {row.code: row.id for row in (await session.scalars(select(Skill))).all()}
    industry = await session.scalar(select(Industry.id).where(Industry.code == "it"))
    await entry_survey(session, industry, specs, skills)
    encoded = hasher.hash(password)
    written = 0
    for item in make_profiles(count, seed, as_of):
        existing = await session.get(core.User, item["id"])
        if existing:
            if existing.email != item["email"]:
                raise ValueError("Идентификатор демоданных занят другим пользователем")
            continue
        category = categories.get(f"{item['specialization']}.{item['grade']}")
        if category is None:
            continue
        uid = item["id"]
        session.add(
            core.User(
                id=uid,
                email=item["email"],
                password_hash=encoded,
                status="active",
                email_verified_at=as_of,
                created_at=item["created_at"],
            )
        )
        await session.flush()
        session.add_all(
            [
                core.UserRoleLink(user_id=uid, role="candidate"),
                core.UserConsent(user_id=uid, kind="pd_processing", document_version="demo"),
                core.UserConsent(user_id=uid, kind="profile_publication", document_version="demo"),
                profile.Candidate(
                    user_id=uid,
                    first_name=item["first_name"],
                    last_name=item["last_name"],
                    city=item["city"],
                    work_format="remote",
                    relocation=True,
                    experience_months=item["experience_months"],
                    about=item["about"],
                    current_category_id=category.id,
                    created_at=item["created_at"],
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                profile.CandidatePrivacy(candidate_id=uid, show_about=True, show_experience=True),
                profile.CandidatePreferences(
                    candidate_id=uid,
                    desired_specialization_id=specs[item["specialization"]],
                    salary_min=item["salary_min"],
                    salary_max=item["salary_max"],
                ),
                profile.FspProfile(candidate_id=uid, verification_status="unlinked"),
                assess.GradeAssignment(
                    candidate_id=uid,
                    category_id=category.id,
                    source="manual",
                    assigned_at=item["created_at"],
                    next_change_allowed_at=as_of,
                    note="Синтетический демонстрационный профиль; тест не пройден",
                    is_current=True,
                ),
            ]
        )
        session.add_all(
            [
                profile.CandidateSkill(
                    candidate_id=uid, skill_id=skills[code], self_level=item["level"], source="self"
                )
                for code in item["skills"]
                if code in skills
            ]
        )
        written += 1
        if written % 100 == 0:
            await session.flush()
    employer_id = uuid.uuid5(NAMESPACE, f"{seed}:employer")
    employer = await session.get(core.User, employer_id)
    if employer is None:
        company_id = uuid.uuid5(NAMESPACE, f"{seed}:company")
        session.add(
            core.User(
                id=employer_id,
                email="employer@scepka.example",
                password_hash=encoded,
                status="active",
                email_verified_at=as_of,
            )
        )
        session.add(
            hire.Company(
                id=company_id,
                name="Сцепка Лаб",
                city="Москва",
                description="Синтетическая продуктовая команда: финтех, поиск и интерфейсы",
            )
        )
        await session.flush()
        session.add(core.UserRoleLink(user_id=employer_id, role="employer"))
        session.add(
            core.UserConsent(user_id=employer_id, kind="pd_processing", document_version="demo")
        )
        session.add(
            hire.Employer(
                user_id=employer_id,
                company_id=company_id,
                full_name="Александра Миронова",
                is_owner=True,
            )
        )
        await session.flush()
        for code, title, description in [
            (
                "backend",
                "Backend для платёжной платформы",
                "Ищем разработчика Python и PostgreSQL. Команда развивает платёжные API, модель данных и обработку событий. Важны транзакции, идемпотентность и код-ревью",
            ),
            (
                "ml_engineer",
                "ML-инженер в команду поиска",
                "Нужен инженер для гибридного поиска, эмбеддингов и переранжирования. Python, PyTorch, RAG, измеримое качество и эксплуатация моделей",
            ),
            (
                "frontend",
                "Frontend для продуктовой команды",
                "Ищем разработчика React и TypeScript. Доступность, дизайн-система, формы и визуализация данных",
            ),
        ]:
            if code not in specs or f"{code}.middle" not in categories:
                continue
            need_id = uuid.uuid5(NAMESPACE, f"{seed}:need:{code}")
            vacancy_id = uuid.uuid5(NAMESPACE, f"{seed}:vacancy:{code}")
            session.add(
                hire.Vacancy(
                    id=vacancy_id,
                    company_id=company_id,
                    author_user_id=employer_id,
                    title=title,
                    description=description,
                    category_id=categories[f"{code}.middle"].id,
                    salary_min=180000,
                    salary_max=360000,
                    work_format="remote",
                    status="published",
                    published_at=as_of,
                )
            )
            await session.flush()
            session.add(
                hire.EmployerNeed(
                    id=need_id,
                    company_id=company_id,
                    author_user_id=employer_id,
                    title=title,
                    description=description,
                    specialization_id=specs[code],
                    grade_id=categories[f"{code}.middle"].grade_id,
                    salary_min=180000,
                    salary_max=360000,
                    vacancy_id=vacancy_id,
                )
            )
            await session.flush()
            for skill in ARCHETYPES[code][0][1]:
                if skill in skills:
                    session.add(
                        hire.NeedSkill(
                            need_id=need_id,
                            skill_id=skills[skill],
                            importance="required",
                            min_level=2,
                        )
                    )
    return {"generated": written, "requested": count, "seed": seed, "demo": True}
