"""Банк шаблонов заданий — сменная часть системы.

Чтобы запустить платформу в другой нише, этот модуль заменяется набором
шаблонов для своих специализаций и грейдов; остальной код не меняется.

Шаблон несёт сложность, ожидаемое время, рубрику проверки и промт генератора.
Сравнимость кандидатов берётся из шаблона, уникальность формулировки — из
генерации экземпляра (``assess.tasks``).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.assess import TaskTemplate
from app.db.models.ref import Grade, Industry, Skill, Specialization

# (отрасль, специализация, грейд, навык|None, код, название, формат,
#  сложность, минуты, макс. балл, промт генератора, рубрика)
TEMPLATES = [
    ("it", "backend", "junior", "postgresql", "be_jun_sql_join",
     "Выборка с соединением и агрегацией", "code", 3, 20, 10.0,
     "Сгенерируй задачу на SQL-выборку с одним JOIN и агрегацией. Предметная область — "
     "случайная из списка: доставка, библиотека, автосервис, стриминг. Схема из 2-3 таблиц "
     "приводится в условии. Ответ — один запрос.",
     "Корректность результата — 6 баллов; отсутствие лишних подзапросов — 2; читаемость — 2."),

    ("it", "backend", "middle", "rest_api", "be_mid_api_design",
     "Проектирование REST-ресурса", "open", 5, 25, 10.0,
     "Опиши продуктовое требование и попроси спроектировать REST-ресурс: пути, методы, коды "
     "ответов, идемпотентность, пагинация. Домен выбирай случайно, объём требования — 5-7 предложений.",
     "Корректные коды ответов — 3; идемпотентность изменяющих операций — 3; пагинация и "
     "фильтрация — 2; версионирование и обработка ошибок — 2."),

    ("it", "backend", "middle", "db_design", "be_mid_schema_fix",
     "Поиск дефектов в схеме БД", "open", 6, 30, 12.0,
     "Приведи DDL на 3-4 таблицы с 3-4 намеренными дефектами (отсутствие FK, денормализация без "
     "причины, тип данных не по смыслу, пропущенный уникальный индекс). Попроси найти дефекты и "
     "предложить исправление.",
     "По 3 балла за каждый найденный дефект с корректным обоснованием; максимум 12."),

    ("it", "backend", "senior", "db_design", "be_sen_concurrency",
     "Конкурентный доступ и блокировки", "case", 8, 35, 15.0,
     "Опиши сценарий гонки при одновременном изменении общего ресурса (остатки на складе, "
     "бронирование мест). Попроси выбрать уровень изоляции и стратегию блокировки с обоснованием.",
     "Диагноз гонки — 5; корректный уровень изоляции с обоснованием — 5; работоспособность "
     "решения под нагрузкой — 5."),

    ("it", "frontend", "junior", "react", "fe_jun_state",
     "Состояние компонента", "code", 3, 20, 10.0,
     "Сгенерируй задачу на управление локальным состоянием в React: форма с валидацией одного "
     "поля и блокировкой кнопки. Требование формулируй как пользовательский сценарий.",
     "Работающая валидация — 5; отсутствие лишних ре-рендеров — 3; доступность — 2."),

    ("it", "frontend", "middle", "typescript", "fe_mid_types",
     "Типизация внешнего контракта", "code", 5, 25, 10.0,
     "Дай JSON-ответ внешнего API со вложенностью и необязательными полями. Попроси описать типы "
     "и безопасный разбор без any.",
     "Отсутствие any — 4; корректная необязательность полей — 3; обработка несоответствия — 3."),

    ("it", "ml_engineer", "middle", "rag", "ml_mid_rag",
     "Выбор стратегии RAG", "case", 6, 30, 12.0,
     "Опиши корпус документов (объём, язык, структуру) и требование к качеству ответов. Попроси "
     "выбрать стратегию разбиения, модель эмбеддингов и способ оценки качества.",
     "Обоснование чанкинга — 4; выбор модели под язык корпуса — 4; измеримые метрики — 4."),

    ("it", "devops", "middle", "kubernetes", "do_mid_k8s_debug",
     "Диагностика падающего пода", "case", 6, 25, 12.0,
     "Приведи вывод kubectl describe/logs с одной из причин отказа (OOMKilled, CrashLoopBackOff "
     "из-за конфига, неготовый readiness). Попроси поставить диагноз и предложить исправление.",
     "Верный диагноз — 6; исправление причины, а не симптома — 4; профилактика повторения — 2."),

    ("it", "qa", "junior", "test_design", "qa_jun_testdesign",
     "Тест-дизайн по требованию", "open", 3, 20, 10.0,
     "Дай короткое требование к форме или API-методу. Попроси составить набор проверок: классы "
     "эквивалентности, граничные значения, негативные сценарии.",
     "Классы эквивалентности — 4; границы — 3; негативные сценарии — 3."),

    ("marketing", "smm", "junior", "content_plan", "mkt_jun_content",
     "Контент-план на неделю", "open", 3, 20, 10.0,
     "Опиши продукт и аудиторию. Попроси составить контент-план на неделю с форматами и "
     "целевыми метриками.",
     "Соответствие аудитории — 4; разнообразие форматов — 3; измеримые метрики — 3."),
]


async def seed_task_bank(session: AsyncSession) -> int:
    industries = {c: i for c, i in await session.execute(select(Industry.code, Industry.id))}
    specs = {
        (ind, code): pk
        for code, ind, pk in await session.execute(
            select(Specialization.code, Specialization.industry_id, Specialization.id)
        )
    }
    grades = {
        (ind, code): pk
        for code, ind, pk in await session.execute(select(Grade.code, Grade.industry_id, Grade.id))
    }
    skills = {c: i for c, i in await session.execute(select(Skill.code, Skill.id))}

    rows = []
    for ind, spec, grade, skill, code, title, fmt, diff, minutes, max_score, prompt, rubric in TEMPLATES:
        ind_id = industries[ind]
        rows.append({
            "specialization_id": specs[(ind_id, spec)],
            "grade_id": grades[(ind_id, grade)],
            "skill_id": skills.get(skill) if skill else None,
            "code": code, "title": title, "format": fmt,
            "difficulty": diff, "expected_minutes": minutes, "max_score": max_score,
            "generator_prompt": prompt, "grading_rubric": rubric, "source": "manual",
        })

    await session.execute(
        insert(TaskTemplate).values(rows).on_conflict_do_nothing(index_elements=["code"])
    )
    return len(rows)
