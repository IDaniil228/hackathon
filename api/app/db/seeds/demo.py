"""Демонстрационные данные под сквозной сценарий из п. 3.3 ТЗ.

Регистрация соискателя → опрос → тест → присвоение категории → поиск
работодателем нужной категории → выход на контакт, плюс классический отклик
на вакансию.

Среди кандидатов намеренно есть и участники ФСП, и люди без истории ФСП:
корректная обработка второго случая обязательна по ТЗ.

``password_hash`` — заглушка фиксированного вида, а не хеш реального пароля:
войти под этими учётками нельзя, они нужны для выборок.
"""

from __future__ import annotations

import hashlib
import random
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import assess, core, hire, matching, profile
from app.db.models.ref import Category, Grade, Industry, Skill, Specialization

PASSWORD_STUB = "$argon2id$v=19$m=65536,t=3,p=4$ZGVtb3NhbHQ$ZGVtb25vdGFyZWFsaGFzaA"
EMBEDDING_MODEL = "demo-random-1024"


def _u(prefix: str, n: int) -> uuid.UUID:
    return uuid.UUID(f"{prefix}-0000-4000-8000-{n:012d}")


CAND = [_u("11111111", i) for i in range(1, 7)]
EMPL = [_u("22222222", i) for i in range(1, 3)]
COMP = [_u("33333333", i) for i in range(1, 3)]
TASK = [_u("44444444", i) for i in range(1, 4)]
TEST_SESSION = _u("55555555", 1)
SURVEY_SESSION = _u("66666666", 1)
VAC = [_u("77777777", i) for i in range(1, 4)]
NEED = [_u("88888888", i) for i in range(1, 3)]
INV = [_u("99999999", i) for i in range(1, 4)]
APP = _u("aaaaaaaa", 1)
RUN = [_u("bbbbbbbb", i) for i in range(1, 3)]
THREAD = _u("cccccccc", 1)
DOC = [_u("dddddddd", i) for i in range(1, 3)]

CANDIDATES = [
    (CAND[0], "anna.kovaleva@example.com", "Анна", "Ковалёва", "Сергеевна", "1996-03-14",
     "+79161234501", "Москва", False, "hybrid", 54,
     "Backend на Python: сервисы с высокой нагрузкой, PostgreSQL, проектирование API. "
     "Призёр командных соревнований ФСП.", "backend.middle", 86.50),
    (CAND[1], "pavel.smirnov@example.com", "Павел", "Смирнов", None, "2001-07-02",
     "+79161234502", "Казань", True, "remote", 11,
     "Год коммерческой разработки на FastAPI, учебные проекты с PostgreSQL. "
     "Истории участия в ФСП нет.", "backend.junior", 41.00),
    (CAND[2], "irina.volkova@example.com", "Ирина", "Волкова", "Андреевна", "1998-11-23",
     "+79161234503", "Санкт-Петербург", False, "remote", 38,
     "Frontend на React и TypeScript, доступность интерфейсов, дизайн-системы.",
     "frontend.middle", 72.00),
    (CAND[3], "denis.orlov@example.com", "Денис", "Орлов", "Игоревич", "1995-01-30",
     "+79161234504", "Новосибирск", True, "hybrid", 62,
     "ML-инженер: NLP, retrieval, инференс в проде. Победитель регионального этапа ФСП.",
     "ml_engineer.middle", 88.00),
    (CAND[4], "marina.lebedeva@example.com", "Марина", "Лебедева", None, "1990-09-09",
     "+79161234505", "Москва", False, "office", 120,
     "DevOps/SRE: Kubernetes, Terraform, наблюдаемость. Веду менторство внутри команды.",
     "devops.senior", 79.50),
    (CAND[5], "oleg.vasilev@example.com", "Олег", "Васильев", None, "2002-05-18",
     "+79161234506", "Екатеринбург", True, "remote", 6,
     "Тестирование: тест-дизайн, автотесты на pytest. Истории участия в ФСП нет.",
     "qa.junior", 33.00),
]

CANDIDATE_SKILLS = [
    (0, "python", 5, 4, 4.5, "test"), (0, "postgresql", 4, 4, 4.0, "test"),
    (0, "fastapi", 4, None, 3.0, "self"), (0, "docker", 3, None, 3.0, "github"),
    (0, "code_review", 4, None, 3.0, "self"),
    (1, "python", 3, 2, 1.0, "test"), (1, "fastapi", 3, None, 1.0, "self"),
    (1, "postgresql", 2, None, 0.5, "self"),
    (2, "typescript", 4, 4, 3.0, "test"), (2, "react", 4, 4, 3.0, "test"),
    (3, "python", 5, 5, 5.0, "test"), (3, "pytorch", 4, 4, 4.0, "test"),
    (3, "transformers", 4, None, 3.0, "github"), (3, "rag", 4, None, 2.0, "self"),
    (4, "kubernetes", 5, 5, 6.0, "test"), (4, "terraform", 4, None, 4.0, "self"),
    (4, "mentoring", 4, None, 5.0, "self"), (4, "tech_leadership", 4, None, 4.0, "self"),
    (5, "pytest", 3, 2, 0.5, "test"), (5, "test_design", 3, 2, 0.5, "test"),
]


def _rand_vector(dim: int = 1024) -> list[float]:
    return [random.uniform(-1.0, 1.0) for _ in range(dim)]


async def clear_demo(session: AsyncSession) -> None:
    """Снести демо-данные. Удаление пользователей каскадом уносит всё их."""
    await session.execute(delete(core.User).where(core.User.id.in_(CAND + EMPL)))
    await session.execute(delete(hire.Company).where(hire.Company.id.in_(COMP)))
    await session.execute(
        delete(assess.SurveyTemplate).where(assess.SurveyTemplate.code == "it_intro_v1")
    )
    await session.execute(delete(profile.FspEvent))


async def seed_demo(session: AsyncSession) -> dict[str, int]:
    now = datetime.now(UTC)
    await clear_demo(session)
    await session.flush()

    cats = {s: i for s, i in await session.execute(select(Category.slug, Category.id))}
    skills = {c: i for c, i in await session.execute(select(Skill.code, Skill.id))}
    it_id = await session.scalar(select(Industry.id).where(Industry.code == "it"))
    specs = {
        c: i
        for c, i in await session.execute(
            select(Specialization.code, Specialization.id).where(Specialization.industry_id == it_id)
        )
    }
    grades = {
        c: i for c, i in await session.execute(select(Grade.code, Grade.id).where(Grade.industry_id == it_id))
    }

    # --- пользователи --------------------------------------------------------
    for i, (uid, email, *_rest) in enumerate(CANDIDATES):
        session.add(core.User(
            id=uid, email=email, password_hash=PASSWORD_STUB, status="active",
            email_verified_at=now - timedelta(days=60 - i * 5),
            created_at=now - timedelta(days=60 - i * 5),
        ))
        session.add(core.UserRoleLink(user_id=uid, role="candidate"))
        session.add(core.UserConsent(user_id=uid, kind="pd_processing", document_version="v1.0"))
        session.add(core.UserConsent(user_id=uid, kind="profile_publication", document_version="v1.0"))

    for uid, email in zip(EMPL, ["hr@technoline.example", "lead@datum.example"]):
        session.add(core.User(
            id=uid, email=email, password_hash=PASSWORD_STUB, status="active",
            email_verified_at=now - timedelta(days=90), created_at=now - timedelta(days=90),
        ))
        session.add(core.UserRoleLink(user_id=uid, role="employer"))
        session.add(core.UserConsent(user_id=uid, kind="pd_processing", document_version="v1.0"))
    await session.flush()

    # --- кандидаты -----------------------------------------------------------
    for (uid, _email, first, last, middle, birth, phone, city, reloc, wf, exp,
         about, cat_slug, strength) in CANDIDATES:
        session.add(profile.Candidate(
            user_id=uid, first_name=first, last_name=last, middle_name=middle,
            birth_date=datetime.strptime(birth, "%Y-%m-%d").date(), phone=phone, city=city,
            relocation=reloc, work_format=wf, employment_type="full_time",
            experience_months=exp, about=about,
            current_category_id=cats[cat_slug], profile_strength=strength,
            profile_refreshed_at=now - timedelta(days=5),
        ))
        session.add(profile.CandidatePrivacy(candidate_id=uid))
    await session.flush()

    prefs = [
        (0, "backend", 250000, 340000, "Продуктовая команда, зрелые процессы ревью, сложные данные",
         "Аутстаффинг, овертаймы, legacy без тестов"),
        (1, "backend", 110000, 160000, "Наставник в команде, понятные задачи, обратная связь",
         "Полная самостоятельность без поддержки"),
        (2, "frontend", 200000, 280000, "Дизайн-система, доступность, собственный продукт",
         "Вёрстка лендингов на потоке"),
        (3, "ml_engineer", 280000, 400000, "Исследовательские задачи с выходом в прод, NLP",
         "Только разметка данных"),
        (4, "devops", 300000, 420000, "Надёжность, SLO, автоматизация", "Ручные релизы по ночам"),
        (5, "qa", 70000, 110000, "Обучение, менторство, автотесты", "Только ручная регрессия"),
    ]
    for idx, spec, lo, hi, pos, neg in prefs:
        session.add(profile.CandidatePreferences(
            candidate_id=CAND[idx], desired_specialization_id=specs[spec],
            salary_min=lo, salary_max=hi, positive_prompt=pos, negative_prompt=neg,
        ))

    for idx, kind, url, label in [
        (0, "github", "https://github.com/anna-kovaleva-demo", "GitHub"),
        (0, "portfolio", "https://kovaleva.example/portfolio", "Портфолио"),
        (2, "github", "https://github.com/irina-volkova-demo", "GitHub"),
        (3, "telegram", "https://t.me/denis_orlov_demo", "Telegram"),
        (4, "gitlab", "https://gitlab.com/m-lebedeva-demo", "GitLab"),
    ]:
        session.add(profile.CandidateLink(candidate_id=CAND[idx], kind=kind, url=url, label=label))

    for idx, skill, self_lvl, conf, years, src in CANDIDATE_SKILLS:
        session.add(profile.CandidateSkill(
            candidate_id=CAND[idx], skill_id=skills[skill], self_level=self_lvl,
            confirmed_level=conf, years=years, source=src,
        ))

    # --- EAV -----------------------------------------------------------------
    from app.db.models.ref import AttributeDef, AttributeOption

    eng_id = await session.scalar(
        select(AttributeDef.id).where(AttributeDef.scope == "candidate", AttributeDef.code == "english_level")
    )
    c1_id = await session.scalar(
        select(AttributeOption.id).where(AttributeOption.attribute_id == eng_id, AttributeOption.code == "c1")
    )
    trips_id = await session.scalar(
        select(AttributeDef.id).where(AttributeDef.scope == "candidate", AttributeDef.code == "business_trips")
    )
    team_id = await session.scalar(
        select(AttributeDef.id).where(AttributeDef.scope == "candidate", AttributeDef.code == "preferred_team_size")
    )
    session.add(profile.CandidateAttributeValue(
        candidate_id=CAND[0], attribute_id=eng_id, data_type="option", option_id=c1_id))
    session.add(profile.CandidateAttributeValue(
        candidate_id=CAND[0], attribute_id=trips_id, data_type="boolean", value_boolean=True))
    session.add(profile.CandidateAttributeValue(
        candidate_id=CAND[3], attribute_id=team_id, data_type="integer", value_integer=6))

    # --- резюме --------------------------------------------------------------
    session.add(profile.ResumeDocument(
        id=DOC[0], candidate_id=CAND[0], kind="uploaded",
        storage_key="resumes/11111111/kovaleva.pdf", file_name="kovaleva.pdf",
        size_bytes=184320, parse_status="done",
        raw_text="Ковалёва Анна Сергеевна. Backend-разработчик. Python, FastAPI, PostgreSQL, Docker. Опыт 4.5 года.",
        parsed_at=now - timedelta(days=58),
    ))
    session.add(profile.ResumeDocument(
        id=DOC[1], candidate_id=CAND[0], kind="generated",
        storage_key="resumes/11111111/profile-generated.pdf", file_name="profile.pdf",
        size_bytes=96000, parse_status="done", parsed_at=now - timedelta(days=3),
    ))
    await session.flush()
    session.add(profile.ResumeParseResult(
        document_id=DOC[0], model="demo-extractor-v1", confidence=0.92,
        applied_at=now - timedelta(days=58),
        payload={"full_name": "Ковалёва Анна Сергеевна", "experience_months": 54,
                 "skills": ["python", "fastapi", "postgresql", "docker"], "city": "Москва"},
    ))

    # --- ФСП: строка есть у каждого, отсутствие истории — это статус ---------
    linked = {0: ("FSP-0000123", "verified"), 3: ("FSP-0000456", "verified"), 4: (None, "pending")}
    for idx in range(6):
        pid, status = linked.get(idx, (None, "unlinked"))
        session.add(profile.FspProfile(
            candidate_id=CAND[idx], fsp_participant_id=pid, verification_status=status,
            linked_at=now - timedelta(days=55) if pid else None,
            synced_at=now - timedelta(days=4) if pid else None,
        ))

    events = {}
    for ext, title, disc, lvl, s, e in [
        ("FSP-EV-2025-01", "Всероссийские соревнования по спортивному программированию",
         "Продуктовое программирование", "federal", "2025-04-12", "2025-04-14"),
        ("FSP-EV-2025-02", "Региональный этап: алгоритмическое программирование",
         "Алгоритмическое программирование", "regional", "2025-09-20", "2025-09-21"),
        ("FSP-EV-2024-07", "Хакатон по программированию систем ИИ",
         "Программирование систем ИИ", "federal", "2024-11-08", "2024-11-10"),
    ]:
        ev = profile.FspEvent(
            external_id=ext, title=title, discipline=disc, level=lvl,
            starts_on=datetime.strptime(s, "%Y-%m-%d").date(),
            ends_on=datetime.strptime(e, "%Y-%m-%d").date(),
        )
        session.add(ev)
        events[ext] = ev
    await session.flush()

    for idx, ext, place, participants, team, score in [
        (0, "FSP-EV-2025-01", 2, 180, "Ctrl+Alt+Win", 87.5),
        (0, "FSP-EV-2024-07", 5, 120, "Ctrl+Alt+Win", 71.0),
        (3, "FSP-EV-2024-07", 1, 120, "Vector Shift", 94.0),
        (3, "FSP-EV-2025-02", 3, 64, "Vector Shift", 80.5),
    ]:
        session.add(profile.FspAchievement(
            candidate_id=CAND[idx], event_id=events[ext].id, place=place,
            participants=participants, team_name=team, score=score,
            source="registry", confirmed_at=now - timedelta(days=20),
        ))

    # --- GitHub --------------------------------------------------------------
    for idx, gid, login in [(0, 100001, "anna-kovaleva-demo"), (3, 100004, "denis-orlov-demo")]:
        session.add(profile.GithubAccount(
            candidate_id=CAND[idx], github_user_id=gid, login=login,
            linked_at=now - timedelta(days=50), indexed_at=now - timedelta(days=3),
        ))
    await session.flush()

    repos = {}
    for idx, rid, full, desc, stars, forks, commits, lang in [
        (0, 900001, "anna-kovaleva-demo/order-service", "Сервис заказов на FastAPI", 34, 6, 214, "Python"),
        (0, 900002, "anna-kovaleva-demo/pg-playground", "Эксперименты с планами запросов", 8, 1, 57, "Python"),
        (3, 900003, "denis-orlov-demo/rag-bench", "Бенчмарк RAG-пайплайнов", 120, 19, 311, "Python"),
    ]:
        r = profile.GithubRepo(
            candidate_id=CAND[idx], github_repo_id=rid, full_name=full, description=desc,
            stars=stars, forks=forks, commits_by_owner=commits, primary_language=lang,
            pushed_at=now - timedelta(days=12), indexed_at=now - timedelta(days=3),
        )
        session.add(r)
        repos[rid] = r
    await session.flush()

    for rid, lang, nbytes in [
        (900001, "Python", 412000), (900001, "Dockerfile", 2100),
        (900002, "Python", 88000), (900002, "PLpgSQL", 15000),
        (900003, "Python", 760000), (900003, "Jupyter Notebook", 230000),
    ]:
        session.add(profile.GithubRepoLanguage(repo_id=repos[rid].id, language=lang, bytes=nbytes))

    for idx, skill, score, evidence in [
        (0, "python", 78.0, {"repos": 2, "bytes": 500000, "recent_push_days": 12}),
        (0, "docker", 45.0, {"repos": 1, "dockerfiles": 1}),
        (3, "python", 91.0, {"repos": 1, "bytes": 760000, "stars": 120}),
        (3, "transformers", 69.0, {"imports": 37}),
    ]:
        session.add(profile.GithubSignal(
            candidate_id=CAND[idx], skill_id=skills[skill], score=score, evidence=evidence))

    # --- опрос ---------------------------------------------------------------
    survey = assess.SurveyTemplate(
        industry_id=it_id, code="it_intro_v1", title="Входной опрос: отрасль и специализация")
    session.add(survey)
    await session.flush()

    questions = []
    for order, fmt, qtext in [
        (1, "single_choice", "Чем вы занимаетесь большую часть рабочего времени?"),
        (2, "multi_choice", "С какими инструментами работали в последний год?"),
        (3, "single_choice", "На какой грейд вы претендуете?"),
    ]:
        q = assess.SurveyQuestion(survey_id=survey.id, order_no=order, format=fmt, text=qtext)
        session.add(q)
        questions.append(q)
    await session.flush()

    options = {}
    for q_no, order, label, spec, skill in [
        (1, 1, "Серверная логика и API", "backend", None),
        (1, 2, "Интерфейсы в браузере", "frontend", None),
        (1, 3, "Модели и данные", "ml_engineer", None),
        (1, 4, "Инфраструктура и релизы", "devops", None),
        (2, 1, "PostgreSQL", None, "postgresql"),
        (2, 2, "Docker", None, "docker"),
        (2, 3, "Kubernetes", None, "kubernetes"),
        (3, 1, "Junior", None, None),
        (3, 2, "Middle", None, None),
        (3, 3, "Senior", None, None),
    ]:
        o = assess.SurveyOption(
            question_id=questions[q_no - 1].id, order_no=order, label=label,
            specialization_id=specs[spec] if spec else None,
            skill_id=skills[skill] if skill else None,
        )
        session.add(o)
        options[(q_no, order)] = o
    await session.flush()

    session.add(assess.SurveySession(
        id=SURVEY_SESSION, candidate_id=CAND[0], survey_id=survey.id, status="scored",
        specialization_id=specs["backend"], claimed_grade_id=grades["middle"],
        started_at=now - timedelta(days=59),
        finished_at=now - timedelta(days=59) + timedelta(minutes=4),
    ))
    await session.flush()
    for key in [(1, 1), (2, 1), (2, 2), (3, 2)]:
        session.add(assess.SurveyAnswer(
            session_id=SURVEY_SESSION, question_id=options[key].question_id, option_id=options[key].id))

    # --- тест ----------------------------------------------------------------
    tpl = {
        c: (pk, d, ms)
        for c, pk, d, ms in await session.execute(
            select(assess.TaskTemplate.code, assess.TaskTemplate.id,
                   assess.TaskTemplate.difficulty, assess.TaskTemplate.max_score)
        )
    }
    task_specs = [
        (TASK[0], "be_mid_api_design",
         "Сервис доставки продуктов. Спроектируйте REST-ресурс для заказа: создание, частичное "
         "изменение состава, отмена. Укажите пути, методы, коды ответов, поведение при повторной "
         "отправке запроса и способ постраничной выдачи истории заказов.",
         {"must_cover": ["idempotency", "pagination", "status_codes", "errors"]}, 10241),
        (TASK[1], "be_mid_schema_fix",
         "Ниже DDL складского учёта. Найдите дефекты проектирования и предложите исправления: "
         "CREATE TABLE items (id serial, name text, qty text); "
         "CREATE TABLE moves (id serial, item_id int, delta text, created text);",
         {"defects": ["no_fk", "wrong_types", "no_pk_constraint", "no_index"]}, 10242),
    ]
    for task_id, code, statement, key, seed in task_specs:
        tpl_id, diff, max_score = tpl[code]
        session.add(assess.Task(
            id=task_id, template_id=tpl_id, candidate_id=CAND[0], statement=statement,
            answer_key=key, difficulty=diff, max_score=max_score,
            generated_by="demo-generator-v1", generation_seed=seed,
        ))
    await session.flush()

    session.add(assess.TestSession(
        id=TEST_SESSION, candidate_id=CAND[0], survey_session_id=SURVEY_SESSION,
        specialization_id=specs["backend"], claimed_grade_id=grades["middle"],
        status="scored", outcome="passed", assigned_category_id=cats["backend.middle"],
        total_score=18.50, max_score=22.00,
        started_at=now - timedelta(days=59),
        deadline_at=now - timedelta(days=59) + timedelta(minutes=90),
        finished_at=now - timedelta(days=59) + timedelta(minutes=63),
        created_at=now - timedelta(days=59),
    ))
    await session.flush()

    for order, task_id in enumerate(TASK[:2], start=1):
        session.add(assess.TestSessionTask(session_id=TEST_SESSION, task_id=task_id, order_no=order))

    for task_id, answer, score, max_score, feedback, spent, paste, focus in [
        (TASK[0], "POST /orders, PATCH /orders/{id}/items, POST /orders/{id}/cancel. Повтор защищён "
                  "ключом Idempotency-Key, история — курсорная пагинация.",
         8.50, 10.00, "Идемпотентность и пагинация раскрыты, не хватает версионирования API.", 1620, 0, 1),
        (TASK[1], "qty и delta должны быть целочисленными, created — timestamptz, нужен FK "
                  "moves.item_id -> items.id и индекс по нему, явный PRIMARY KEY.",
         10.00, 12.00, "Найдены все дефекты, исправления корректны.", 1740, 0, 0),
    ]:
        session.add(assess.TaskAnswer(
            session_id=TEST_SESSION, task_id=task_id, answer_text=answer, score=score,
            max_score=max_score, checked_by="llm", feedback=feedback,
            submitted_at=now - timedelta(days=59) + timedelta(minutes=30),
            checked_at=now - timedelta(days=59) + timedelta(minutes=70),
            time_spent_sec=spent, paste_count=paste, focus_lost_count=focus,
        ))

    session.add(assess.GradeAssignment(
        candidate_id=CAND[0], category_id=cats["backend.middle"], source="test",
        test_session_id=TEST_SESSION, assigned_at=now - timedelta(days=59),
        next_change_allowed_at=now - timedelta(days=59) + timedelta(days=90), is_current=True,
    ))
    for idx in range(1, 6):
        session.add(assess.GradeAssignment(
            candidate_id=CAND[idx], category_id=cats[CANDIDATES[idx][12]], source="import",
            assigned_at=now - timedelta(days=30),
            next_change_allowed_at=now - timedelta(days=30) + timedelta(days=90),
            is_current=True, note="Демо-данные",
        ))

    # --- работодатели --------------------------------------------------------
    session.add(hire.Company(
        id=COMP[0], name="ТехноЛайн", legal_name="ООО «ТехноЛайн»", inn="7701234567",
        industry_id=it_id, description="Продуктовая компания: сервисы для логистики, 120 человек в разработке.",
        website="https://technoline.example", size="medium", city="Москва", is_verified=True))
    session.add(hire.Company(
        id=COMP[1], name="Датум", legal_name="АО «Датум»", inn="7809876543",
        industry_id=it_id, description="Платформа аналитики и ML-сервисов для ритейла.",
        website="https://datum.example", size="small", city="Санкт-Петербург"))
    await session.flush()

    session.add(hire.Employer(
        user_id=EMPL[0], company_id=COMP[0], full_name="Светлана Жукова",
        position="Руководитель подбора", contact_email="hr@technoline.example",
        contact_phone="+74951234567", is_owner=True))
    session.add(hire.Employer(
        user_id=EMPL[1], company_id=COMP[1], full_name="Артём Белов",
        position="Технический директор", contact_email="lead@datum.example",
        contact_phone="+78121234567", is_owner=True))

    vacancies = [
        (VAC[0], COMP[0], EMPL[0], "Backend-разработчик (Python)",
         "Команда логистической платформы: сервисы маршрутизации и расчёта стоимости. Стек: Python, "
         "FastAPI, PostgreSQL, Kubernetes. Нужен человек, который умеет проектировать схему данных "
         "и держать нагрузку.", "backend.middle", "hybrid", "Москва", 240000, 320000, 14),
        (VAC[1], COMP[1], EMPL[1], "ML-инженер (NLP)",
         "Поиск и ранжирование товарных карточек: эмбеддинги, retrieval, оценка качества выдачи. "
         "Нужен опыт вывода моделей в прод.", "ml_engineer.middle", "remote", None, 280000, 380000, 9),
        (VAC[2], COMP[0], EMPL[0], "Frontend-разработчик (React)",
         "Личный кабинет клиента: React, TypeScript, дизайн-система, доступность.",
         "frontend.middle", "remote", None, 210000, 270000, 7),
    ]
    for vid, comp, author, title, desc, cat, wf, city, lo, hi, days in vacancies:
        session.add(hire.Vacancy(
            id=vid, company_id=comp, author_user_id=author, title=title, description=desc,
            category_id=cats[cat], work_format=wf, employment_type="full_time", city=city,
            salary_min=lo, salary_max=hi, status="published",
            published_at=now - timedelta(days=days),
        ))
    await session.flush()

    for vid, skill, importance, lvl in [
        (VAC[0], "python", "required", 4), (VAC[0], "postgresql", "required", 3),
        (VAC[0], "fastapi", "nice_to_have", 2), (VAC[0], "kubernetes", "nice_to_have", 2),
        (VAC[1], "python", "required", 4), (VAC[1], "pytorch", "required", 3),
        (VAC[1], "rag", "nice_to_have", 2),
        (VAC[2], "typescript", "required", 3), (VAC[2], "react", "required", 3),
    ]:
        session.add(hire.VacancySkill(
            vacancy_id=vid, skill_id=skills[skill], importance=importance, min_level=lvl))

    session.add(hire.EmployerNeed(
        id=NEED[0], company_id=COMP[0], author_user_id=EMPL[0], vacancy_id=VAC[0],
        title="Backend в команду маршрутизации",
        description="Команда из шести человек отвечает за расчёт маршрутов и стоимости доставки. "
                    "Ищем разработчика, который самостоятельно спроектирует схему данных под новый "
                    "тариф и доведёт сервис до прода.",
        specialization_id=specs["backend"], grade_id=grades["middle"], category_id=cats["backend.middle"],
        positive_prompt="Опыт проектирования схемы БД, понимание планов запросов, участие в соревнованиях приветствуется",
        negative_prompt="Только фреймворк без понимания данных; отсутствие опыта работы с нагрузкой",
        salary_min=240000, salary_max=320000))
    session.add(hire.EmployerNeed(
        id=NEED[1], company_id=COMP[1], author_user_id=EMPL[1],
        title="ML-инженер для поиска по каталогу",
        description="Нужен человек, который выстроит retrieval по товарному каталогу: эмбеддинги, "
                    "переранжирование, измеримое качество выдачи.",
        specialization_id=specs["ml_engineer"], grade_id=grades["middle"],
        category_id=cats["ml_engineer.middle"],
        positive_prompt="Практика RAG, собственные бенчмарки, вывод моделей в прод",
        negative_prompt="Только академические проекты без эксплуатации",
        salary_min=280000, salary_max=400000))
    await session.flush()

    for nid, skill, importance, lvl in [
        (NEED[0], "python", "required", 4), (NEED[0], "postgresql", "required", 3),
        (NEED[0], "db_design", "required", 3),
        (NEED[1], "pytorch", "required", 3), (NEED[1], "rag", "required", 2),
    ]:
        session.add(hire.NeedSkill(need_id=nid, skill_id=skills[skill], importance=importance, min_level=lvl))

    # --- приглашения ---------------------------------------------------------
    session.add(hire.Invitation(
        id=INV[0], company_id=COMP[0], sender_user_id=EMPL[0], candidate_id=CAND[0],
        vacancy_id=VAC[0], need_id=NEED[0],
        message="Анна, увидели подтверждённый профиль backend middle и призовое место на "
                "всероссийских соревнованиях. Предлагаем команду маршрутизации, гибрид в Москве.",
        salary_min=260000, salary_max=320000, contact_method="email",
        contact_value="hr@technoline.example", status="accepted",
        sent_at=now - timedelta(days=6), viewed_at=now - timedelta(days=6) + timedelta(hours=3),
        responded_at=now - timedelta(days=5)))
    session.add(hire.Invitation(
        id=INV[1], company_id=COMP[0], sender_user_id=EMPL[0], candidate_id=CAND[1],
        vacancy_id=VAC[0], need_id=NEED[0],
        message="Павел, рассматриваем junior-позицию в ту же команду с наставником.",
        salary_min=120000, salary_max=160000, status="sent", sent_at=now - timedelta(days=2)))
    session.add(hire.Invitation(
        id=INV[2], company_id=COMP[1], sender_user_id=EMPL[1], candidate_id=CAND[3],
        need_id=NEED[1],
        message="Денис, ищем ML-инженера на поиск по каталогу. Приглашение без привязки к "
                "опубликованной вакансии.",
        salary_min=300000, salary_max=400000, contact_method="telegram", contact_value="@datum_lead",
        status="viewed", sent_at=now - timedelta(days=3), viewed_at=now - timedelta(days=2)))
    await session.flush()

    for inv, frm, to, actor, delta in [
        (INV[0], None, "sent", EMPL[0], timedelta(days=6)),
        (INV[0], "sent", "viewed", CAND[0], timedelta(days=6) - timedelta(hours=3)),
        (INV[0], "viewed", "accepted", CAND[0], timedelta(days=5)),
        (INV[1], None, "sent", EMPL[0], timedelta(days=2)),
        (INV[2], None, "sent", EMPL[1], timedelta(days=3)),
        (INV[2], "sent", "viewed", CAND[3], timedelta(days=2)),
    ]:
        session.add(hire.InvitationEvent(
            invitation_id=inv, from_status=frm, to_status=to, actor_user_id=actor,
            created_at=now - delta))

    session.add(hire.Application(
        id=APP, vacancy_id=VAC[2], candidate_id=CAND[2],
        cover_letter="Собрала дизайн-систему на прошлом месте, интересен личный кабинет с "
                     "требованиями по доступности.",
        status="viewed", created_at=now - timedelta(days=4), viewed_at=now - timedelta(days=3)))
    await session.flush()
    session.add(hire.ApplicationEvent(
        application_id=APP, to_status="sent", actor_user_id=CAND[2], created_at=now - timedelta(days=4)))
    session.add(hire.ApplicationEvent(
        application_id=APP, from_status="sent", to_status="viewed", actor_user_id=EMPL[0],
        created_at=now - timedelta(days=3)))

    # Контакты раскрыты ровно там, где для этого есть основание
    session.add(hire.ContactDisclosure(
        candidate_id=CAND[0], company_id=COMP[0], reason="invitation_accepted",
        invitation_id=INV[0], granted_at=now - timedelta(days=5)))
    session.add(hire.ContactDisclosure(
        candidate_id=CAND[2], company_id=COMP[0], reason="application_sent",
        application_id=APP, granted_at=now - timedelta(days=4)))

    # --- регулярное задание --------------------------------------------------
    tpl_id, diff, _ = tpl["be_mid_schema_fix"]
    session.add(assess.Task(
        id=TASK[2], template_id=tpl_id, candidate_id=CAND[0],
        statement="Коротко: как бы вы организовали хранение тарифов, меняющихся во времени, чтобы "
                  "исторические заказы считались по тарифу на момент заказа?",
        difficulty=diff, max_score=5.00, generated_by="demo-generator-v1", generation_seed=10243))
    await session.flush()
    session.add(assess.RegularAssignment(
        candidate_id=CAND[0], task_id=TASK[2], company_id=COMP[0], status="scored",
        answer_text="Версионирование тарифов интервалами действия, в заказе — ссылка на конкретную "
                    "версию, а не на тариф.",
        score=4.50, max_score=5.00, issued_at=now - timedelta(days=4),
        due_at=now - timedelta(days=1), answered_at=now - timedelta(days=3)))

    # --- подбор --------------------------------------------------------------
    # Вектора случайные: демонстрируют работоспособность индекса и запросов,
    # но не семантику. Реальные значения пишет сервис эмбеддингов.
    random.seed(20261006)
    for uid in CAND:
        session.add(matching.CandidateEmbedding(
            candidate_id=uid, model=EMBEDDING_MODEL, embedding=_rand_vector(),
            source_hash=hashlib.md5(str(uid).encode()).hexdigest()))
    for vid in VAC:
        session.add(matching.VacancyEmbedding(
            vacancy_id=vid, model=EMBEDDING_MODEL, embedding=_rand_vector(),
            source_hash=hashlib.md5(str(vid).encode()).hexdigest()))
    for nid in NEED:
        session.add(matching.NeedEmbedding(
            need_id=nid, model=EMBEDDING_MODEL, embedding=_rand_vector(),
            source_hash=hashlib.md5(str(nid).encode()).hexdigest()))

    session.add(matching.SearchRun(
        id=RUN[0], need_id=NEED[0], author_user_id=EMPL[0], strategy="prefilter_vector_llm",
        params={"pool": 120, "fresh_share": 0.2, "random_share": 0.1, "vector_top_k": 40,
                "llm_top_k": 10, "min_vector_score": 0.55},
        status="done", pool_size=120, llm_model="demo-llm-v1",
        summary="В категории backend.middle отобрано 10 кандидатов. Выше остальных — профили с "
                "подтверждённым навыком проектирования БД и достижениями ФСП.",
        created_at=now - timedelta(days=7),
        finished_at=now - timedelta(days=7) + timedelta(seconds=38)))
    session.add(matching.SearchRun(
        id=RUN[1], need_id=NEED[0], author_user_id=EMPL[0], parent_run_id=RUN[0],
        strategy="prefilter_vector_llm",
        params={"pool": 120, "vector_top_k": 40, "llm_top_k": 10,
                "filters": {"city": "Москва", "relocation": False}},
        status="done", pool_size=64, llm_model="demo-llm-v1",
        summary="Сузили до Москвы без релокации: осталось 4 кандидата, порядок сохранён.",
        created_at=now - timedelta(days=7) + timedelta(minutes=6),
        finished_at=now - timedelta(days=7) + timedelta(minutes=6, seconds=31)))
    await session.flush()

    for run, idx, stage, rank, vec, llm, final, expl in [
        (RUN[0], 0, "llm", 1, 0.84210, 0.93000, 0.90000,
         "Подтверждённый backend.middle, навык проектирования БД подтверждён тестом (10 из 12), "
         "призовое место на всероссийских соревнованиях ФСП. Вилка кандидата пересекается с вилкой потребности."),
        (RUN[0], 4, "llm", 2, 0.71050, 0.62000, 0.65000,
         "Сильный инфраструктурный профиль, но специализация devops, а не backend: подходит как "
         "смежный вариант, если расширить потребность."),
        (RUN[0], 1, "llm", 3, 0.66400, 0.41000, 0.47000,
         "Совпадает стек, но грейд junior против требуемого middle и нет подтверждённого навыка "
         "проектирования БД."),
        (RUN[0], 3, "vector", 4, 0.58900, None, None, None),
        (RUN[0], 2, "prefilter", 5, None, None, None, None),
        (RUN[1], 0, "llm", 1, 0.84210, 0.93000, 0.91000,
         "Москва, релокация не требуется — условие уточнения выполнено, позиция в выдаче сохранена."),
        (RUN[1], 4, "llm", 2, 0.71050, 0.60000, 0.63000,
         "Москва, офис. Специализация по-прежнему смежная."),
    ]:
        session.add(matching.SearchResult(
            run_id=run, candidate_id=CAND[idx], stage=stage, rank=rank,
            vector_score=vec, llm_score=llm, final_score=final, explanation=expl))

    session.add(matching.ChatThread(
        id=THREAD, need_id=NEED[0], user_id=EMPL[0],
        title="Подборка: backend в команду маршрутизации",
        created_at=now - timedelta(days=7),
        updated_at=now - timedelta(days=7) + timedelta(minutes=7)))
    await session.flush()

    for run, role, content, tokens, delta in [
        (RUN[0], "assistant", "Подобрала 10 кандидатов в категории backend.middle. Первый — с "
                              "подтверждённым навыком проектирования БД и призовым местом ФСП.",
         64, timedelta(days=7)),
        (None, "user", "Оставь только Москву и тех, кому не нужна релокация.", 18,
         timedelta(days=7) - timedelta(minutes=5)),
        (RUN[1], "assistant", "Уточнила: осталось 4 кандидата. Предыдущая подборка сохранена, к "
                              "ней можно вернуться.", 42, timedelta(days=7) - timedelta(minutes=6)),
    ]:
        session.add(matching.ChatMessage(
            thread_id=THREAD, run_id=run, role=role, content=content, tokens=tokens,
            created_at=now - delta))

    return {"candidates": len(CAND), "companies": len(COMP), "invitations": len(INV)}
