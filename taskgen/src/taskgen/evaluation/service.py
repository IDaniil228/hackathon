"""Оценка ответов и сессий: связывает песочницу, анализаторы, судью, формулу балла и summary.

Содержимое tasks.hidden_payload описывает HiddenPayload в taskgen.contracts. Ни одно его поле
не уходит наружу: кандидат и работодатель видят только SessionResult.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from taskgen.config import ScoringSettings, Settings
from taskgen.contracts import (
    AIDetector,
    AnalysisReport,
    Analyzer,
    HiddenPayload,
    Judge,
    JudgeRequest,
    JudgeResult,
    LLMClient,
    RunLimits,
    SandboxRunner,
    SessionStatus,
    TaskKind,
    TaskStatus,
    TestReport,
    TestRunRequest,
)
from taskgen.db import Answer, CandidateSummary, Grade, Task, Template, TestSession
from taskgen.evaluation.analyzers import default_analyzers, run_analyzers
from taskgen.evaluation.detector import LLMAIDetector
from taskgen.evaluation.evidence import EvidenceOptions
from taskgen.evaluation.grading import (
    GradeLevel,
    LevelResult,
    Outcome,
    Recommendation,
    TaskScore,
    assess_grade,
)
from taskgen.evaluation.judge import JudgeError, LLMJudge
from taskgen.evaluation.languages import canonical_language
from taskgen.evaluation.metrics import MetricScore, compute_quality, judge_facts, load_rubric
from taskgen.evaluation.quotes import QuoteRelevance, state_embedding_cache
from taskgen.evaluation.scoring import (
    Components,
    SessionScore,
    code_task_score,
    performance_score,
    quiz_score,
    resolve_weights,
    session_score,
)
from taskgen.evaluation.summary import SessionFacts, fallback_text, summary_data, write_summary
from taskgen.llm import limited_llm, state_embedder
from taskgen.sandbox.errors import SandboxError

logger = logging.getLogger(__name__)

SCORED = SessionStatus.SCORED

# Почему сессию в этом статусе нельзя оценить; ready и scored оценивать можно.
_NOT_SCORABLE = {
    SessionStatus.GENERATING: (
        "Сессия ещё генерируется: оценка возможна, когда готовы все задания"
    ),
    SessionStatus.FAILED: ("Генерация сессии не удалась, оценивать нечего; создайте новую сессию"),
}


# --- Ошибки, которые API переводит в HTTP-ответы ---------------------------------------


class EvaluationError(Exception):
    """Базовая ошибка оценки; текст сообщения уходит клиенту."""


class NotFoundError(EvaluationError):
    pass


class ConflictError(EvaluationError):
    pass


class InvalidAnswerError(EvaluationError):
    pass


class SandboxUnavailableError(EvaluationError):
    pass


class JudgeUnavailableError(EvaluationError):
    pass


class BrokenTaskError(EvaluationError):
    pass


# --- Модели --------------------------------------------------------------------------------


class Behavior(BaseModel):
    """Поведенческие сигналы фронтенда. Это описательные факты, на балл они не влияют."""

    paste_count: int | None = Field(default=None, ge=0, description="Число вставок из буфера")
    focus_lost_count: int | None = Field(
        default=None, ge=0, description="Сколько раз кандидат уходил со страницы теста"
    )
    time_spent_sec: float | None = Field(
        default=None, ge=0, description="Время работы над заданием, секунды"
    )


class AnswerInput(BaseModel):
    content: str
    language: str | None = None
    test_report: dict[str, Any] | None = None
    analysis: list[dict[str, Any]] | None = None
    behavior: dict[str, Any] = {}


@dataclass
class TaskInput:
    task_id: uuid.UUID
    position: int
    kind: TaskKind
    grade: str
    statement: str
    hidden: HiddenPayload
    answer: AnswerInput | None


class TestsSummary(BaseModel):
    __test__ = False

    passed: int
    total: int
    compile_error: bool


class TaskOutcome(BaseModel):
    task_id: uuid.UUID
    position: int
    kind: TaskKind
    grade: str
    answered: bool
    language: str | None = None
    score: float
    components: Components | None = None
    weights: dict[str, float] | None = None
    quality_items: list[MetricScore] = []
    tests: TestsSummary | None = None
    judge: JudgeResult | None = None
    behavior: Behavior | None = None


class JudgeCounts(BaseModel):
    """Итог судьи без идентификаторов пунктов: рубрика — часть скрытых данных задания."""

    yes: int
    partial: int
    no: int
    spread: float


class TaskResult(BaseModel):
    task_id: uuid.UUID
    position: int
    kind: TaskKind
    grade: str
    answered: bool
    language: str | None
    score: float = Field(description="Балл задания от 0 до 1")
    solved: bool = Field(description="Балл не ниже порога решённости")
    components: Components | None = Field(
        description="Компоненты балла задания с кодом; null — компонент не измерен"
    )
    tests: TestsSummary | None
    judge: JudgeCounts | None
    behavior: Behavior | None


class Thresholds(BaseModel):
    solved: float = Field(description="Балл, с которого задание считается решённым")
    pass_level: float = Field(description="Доля решённых заданий, с которой уровень пройден")


class SessionResult(BaseModel):
    session_id: uuid.UUID
    claimed_grade: str
    assigned_grade: str | None = Field(description="Подтверждённый грейд; null — ни один")
    outcome: Outcome = Field(
        description="passed, upgraded, downgraded или failed — вердикт по уровням; "
        "insufficient — заданий с кодом и эссе заявленного уровня меньше "
        "min_evidence_per_level, и вердикта нет"
    )
    recommendation: Recommendation
    levels: list[LevelResult] = Field(
        description="Уровни по заданиям с кодом и эссе; тестовые задачки сюда не входят"
    )
    thresholds: Thresholds
    score: SessionScore = Field(
        description="Общий балл: средний балл заданий с кодом и эссе и блок тестовых задачек "
        "с весом weight_quiz"
    )
    tasks: list[TaskResult]


class Submission(BaseModel):
    """Ответ кандидата: content для кода и эссе, choice — id варианта для задачки."""

    content: str | None = None
    choice: str | None = None
    language: str | None = None
    behavior: Behavior = Behavior()


# --- Оценка одного задания -----------------------------------------------------------------


def _judge_counts(result: JudgeResult | None) -> JudgeCounts | None:
    if result is None:
        return None
    verdicts = [verdict.verdict for verdict in result.verdicts]
    return JudgeCounts(
        yes=verdicts.count("yes"),
        partial=verdicts.count("partial"),
        no=verdicts.count("no"),
        spread=result.spread,
    )


async def evaluate_task(task: TaskInput, *, judge: Judge, scoring: ScoringSettings) -> TaskOutcome:
    """Балл одного задания. Из внешнего мира трогает только судью."""
    base = {
        "task_id": task.task_id,
        "position": task.position,
        "kind": task.kind,
        "grade": task.grade,
    }
    answer = task.answer
    if answer is None:
        return TaskOutcome(**base, answered=False, score=0.0)
    behavior = Behavior.model_validate(answer.behavior)
    base |= {"answered": True, "language": answer.language, "behavior": behavior}
    rubric = task.hidden.rubric

    if task.kind == TaskKind.QUIZ:
        if task.hidden.answer_key is None:
            raise BrokenTaskError(f"У задачки {task.task_id} нет ключа ответа")
        return TaskOutcome(**base, score=quiz_score(answer.content, task.hidden.answer_key))

    if task.kind == TaskKind.ESSAY:
        if not rubric:
            logger.warning("Задание %s без рубрики: развёрнутый ответ не оценить", task.task_id)
            return TaskOutcome(**base, score=0.0)
        result = await judge.judge(
            JudgeRequest(
                kind=task.kind, statement=task.statement, answer=answer.content, rubric=rubric
            )
        )
        return TaskOutcome(**base, score=result.score, judge=result)

    report = (
        TestReport.model_validate(answer.test_report)
        if answer.test_report is not None
        else TestReport(outcomes=[])
    )
    tests = TestsSummary(
        passed=sum(outcome.passed for outcome in report.outcomes),
        total=len(report.outcomes),
        compile_error=report.compile_error is not None,
    )
    reports = [AnalysisReport.model_validate(item) for item in answer.analysis or []]
    metric_rubric = load_rubric(answer.language or task.hidden.runtime)
    quality = compute_quality(reports, metric_rubric) if metric_rubric else None
    weights = resolve_weights(scoring, task.hidden.scoring)

    judge_result = None
    # При нулевом прогоне балл всё равно 0 по гейту, и вызов судьи ничего не изменит.
    if report.pass_rate > 0 and rubric:
        facts = {
            "тесты": {
                "пройдено": tests.passed,
                "всего": tests.total,
                "ошибка компиляции": tests.compile_error,
            },
            "метрики": judge_facts(reports, metric_rubric),
        }
        judge_result = await judge.judge(
            JudgeRequest(
                kind=task.kind,
                statement=task.statement,
                answer=answer.content,
                rubric=rubric,
                facts=facts,
            )
        )
    components = Components(
        tests=report.pass_rate,
        rubric=judge_result.score if judge_result else None,
        quality=quality.score if quality else None,
        performance=performance_score(report, task.hidden.reference_report),
    )
    return TaskOutcome(
        **base,
        score=code_task_score(components, weights),
        components=components,
        weights=weights,
        quality_items=quality.items if quality else [],
        tests=tests,
        judge=judge_result,
    )


# --- Зависимости ---------------------------------------------------------------------------


@dataclass
class Evaluator:
    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    llm: LLMClient
    judge: Judge
    sandbox: SandboxRunner | None
    analyzers: list[Analyzer]
    detector: AIDetector | None

    @classmethod
    def from_state(cls, state: Any) -> Evaluator:
        """Собирает зависимости из app.state; всё, чего там нет, берётся по умолчанию.

        Песочницы по умолчанию нет: без app.state.sandbox ответы с кодом не принимаются.
        Детектор ИИ работает только при ai_detector_enabled, даже если он задан в state.
        """
        settings: Settings = state.settings
        llm = limited_llm(state)
        threshold = settings.scoring.judge_quote_threshold
        relevance = (
            None
            if threshold is None
            else QuoteRelevance(
                threshold=threshold,
                embedder=state_embedder(state),
                cache=state_embedding_cache(state),
            )
        )
        judge = getattr(state, "judge", None) or LLMJudge(
            llm,
            model=settings.llm.judge_model,
            samples=settings.scoring.judge_samples,
            temperature=settings.scoring.judge_temperature,
            reasoning_effort=settings.llm.judge_reasoning_effort,
            relevance=relevance,
            evidence=EvidenceOptions(
                mode=settings.scoring.judge_evidence,
                repairs=settings.scoring.judge_repairs,
                max_spans=settings.scoring.judge_max_spans,
            ),
        )
        detector = None
        if settings.ai_detector_enabled:
            detector = getattr(state, "ai_detector", None) or LLMAIDetector(
                llm,
                model=settings.llm.judge_model,
                reasoning_effort=settings.llm.judge_reasoning_effort,
            )
        analyzers = getattr(state, "analyzers", None)
        return cls(
            settings=settings,
            sessionmaker=state.sessionmaker,
            llm=llm,
            judge=judge,
            sandbox=getattr(state, "sandbox", None),
            analyzers=analyzers
            if analyzers is not None
            else default_analyzers(settings.scoring.analyzer_timeout_seconds),
            detector=detector,
        )


def _hidden(task: Task) -> HiddenPayload:
    try:
        return HiddenPayload.model_validate(task.hidden_payload)
    except ValidationError as error:
        raise BrokenTaskError(f"Скрытые данные задания {task.id} повреждены") from error


def _same_language(left: str, right: str) -> bool:
    return (canonical_language(left) or left.strip().lower()) == (
        canonical_language(right) or right.strip().lower()
    )


# --- Приём ответа --------------------------------------------------------------------------


def _answer_content(task: Task, submission: Submission) -> str:
    """Что хранится в answers.content: id варианта для задачки, иначе текст ответа."""
    if task.kind != TaskKind.QUIZ:
        if submission.choice is not None or not submission.content:
            raise InvalidAnswerError(
                "На это задание отвечают полем content; choice — только для тестовой задачки"
            )
        return submission.content
    if submission.content is not None or submission.choice is None:
        raise InvalidAnswerError("На тестовую задачку отвечают полем choice — id варианта")
    known = [option["id"] for option in (task.public_payload or {}).get("options", [])]
    if submission.choice not in known:
        raise InvalidAnswerError(
            f"Варианта {submission.choice!r} у задачки нет; допустимы: {', '.join(known)}"
        )
    return submission.choice


async def check_code(
    ev: Evaluator, hidden: HiddenPayload, content: str, language: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Прогон тестов в песочнице и анализаторы: то, что хранится в ответе с кодом."""
    if ev.sandbox is None:
        raise SandboxUnavailableError("Песочница для прогона тестов не настроена")
    request = TestRunRequest(
        runtime=hidden.runtime or "",
        entrypoint=hidden.entrypoint or "",
        solution=content,
        cases=hidden.cases,
        limits=RunLimits(
            timeout_ms=ev.settings.sandbox.run_timeout_ms,
            memory_bytes=ev.settings.sandbox.run_memory_limit_bytes,
        ),
    )
    try:
        report = await ev.sandbox.run_tests(request)
    except (httpx.HTTPError, SandboxError) as error:
        # PistonRunner отдаёт сетевую ошибку и отказ Piston как SandboxError.
        raise SandboxUnavailableError("Песочница недоступна, повторите позже") from error
    # Анализаторы запускают внешние процессы; в потоке они не блокируют цикл событий.
    reports = await asyncio.to_thread(run_analyzers, ev.analyzers, content, language)
    return report.model_dump(mode="json"), [item.model_dump(mode="json") for item in reports]


async def submit_answer(ev: Evaluator, task_id: uuid.UUID, submission: Submission) -> uuid.UUID:
    """Сохраняет ответ; для кода сразу прогоняет тесты и анализаторы.

    Повторная отправка до оценки сессии заменяет прежний ответ целиком.
    """
    async with ev.sessionmaker() as db:
        row = (
            await db.execute(
                select(Task, TestSession.status)
                .join(TestSession, Task.session_id == TestSession.id)
                .where(Task.id == task_id)
            )
        ).one_or_none()
    if row is None:
        raise NotFoundError("Задание не найдено")
    task, status = row
    if status == SCORED:
        raise ConflictError("Сессия уже оценена, ответы больше не принимаются")
    # Ответ на готовое задание принимается и пока остальные задания сессии генерируются.
    if task.status != TaskStatus.READY:
        raise ConflictError("Задание ещё не готово или не создано, ответ на него не принимается")
    hidden = _hidden(task)
    language = submission.language
    test_report = analysis = None
    content = _answer_content(task, submission)

    if task.kind == TaskKind.CODE:
        if not (hidden.cases and hidden.entrypoint and hidden.runtime):
            raise BrokenTaskError(f"У задания {task.id} нет скрытых тестов")
        if language and not _same_language(language, hidden.runtime):
            raise InvalidAnswerError(
                f"Ответ на языке {language}, а задание решается на {hidden.runtime}"
            )
        language = language or hidden.runtime
        test_report, analysis = await check_code(ev, hidden, content, language)

    ai_signal = None
    # В ответе на задачку только id варианта: детектору нечего читать.
    if ev.detector is not None and task.kind != TaskKind.QUIZ:
        signal = await ev.detector.detect(content, language or "text")
        ai_signal = signal.model_dump(mode="json")

    values = {
        "content": content,
        "language": language,
        "behavior": submission.behavior.model_dump(mode="json"),
        "test_report": test_report,
        "analysis": analysis,
        "ai_signal": ai_signal,
        "judge": None,
        "components": None,
        "score": None,
    }
    statement = (
        insert(Answer)
        .values(id=uuid.uuid4(), task_id=task_id, **values)
        .on_conflict_do_update(index_elements=[Answer.task_id], set_=values)
        .returning(Answer.id)
    )
    async with ev.sessionmaker() as db, db.begin():
        return (await db.execute(statement)).scalar_one()


# --- Оценка сессии -------------------------------------------------------------------------


def _task_result(outcome: TaskOutcome, solved_threshold: float) -> TaskResult:
    return TaskResult(
        task_id=outcome.task_id,
        position=outcome.position,
        kind=outcome.kind,
        grade=outcome.grade,
        answered=outcome.answered,
        language=outcome.language,
        score=outcome.score,
        # Тот же допуск, что в оценке грейда: балл 0.7 может прийти как 0.6999999999999999.
        solved=outcome.score >= solved_threshold - 1e-9,
        components=outcome.components,
        tests=outcome.tests,
        judge=_judge_counts(outcome.judge),
        behavior=outcome.behavior,
    )


async def score_session(ev: Evaluator, session_id: uuid.UUID) -> SessionResult:
    """Оценивает все задания сессии, записывает результат и обновляет summary кандидата."""
    async with ev.sessionmaker() as db:
        session = await db.get(TestSession, session_id)
        if session is None:
            raise NotFoundError("Сессия не найдена")
        if session.status not in (SessionStatus.READY, SessionStatus.SCORED):
            # Неполный набор заданий исказил бы оценку грейда по уровням.
            raise ConflictError(
                _NOT_SCORABLE.get(
                    session.status, f"Сессию в статусе {session.status} оценить нельзя"
                )
            )
        rows = (
            await db.execute(
                select(Task, Template.grade_code, Answer)
                .join(Template, Task.template_id == Template.id)
                .outerjoin(Answer, Answer.task_id == Task.id)
                .where(Task.session_id == session_id)
                .order_by(Task.position)
            )
        ).all()
        grades = (await db.scalars(select(Grade))).all()
    if not rows:
        raise ConflictError("В сессии нет заданий")

    inputs = [
        TaskInput(
            task_id=task.id,
            position=task.position,
            kind=TaskKind(task.kind),
            grade=grade_code,
            statement=task.statement,
            hidden=_hidden(task),
            answer=AnswerInput(
                content=answer.content,
                language=answer.language,
                test_report=answer.test_report,
                analysis=answer.analysis,
                behavior=answer.behavior or {},
            )
            if answer is not None
            else None,
        )
        for task, grade_code, answer in rows
    ]
    try:
        outcomes = await asyncio.gather(
            *(evaluate_task(item, judge=ev.judge, scoring=ev.settings.scoring) for item in inputs)
        )
    except (JudgeError, httpx.HTTPError) as error:
        raise JudgeUnavailableError("Судья недоступен, сессия не оценена") from error

    solved = ev.settings.scoring.solved_threshold
    assessment = assess_grade(
        [
            TaskScore(grade=outcome.grade, score=outcome.score, kind=outcome.kind)
            for outcome in outcomes
        ],
        [GradeLevel(code=grade.code, level=grade.level) for grade in grades],
        session.claimed_grade_code,
        solved_threshold=solved,
        pass_threshold=ev.settings.scoring.pass_threshold,
        min_evidence=ev.settings.scoring.min_evidence_per_level,
    )
    result = SessionResult(
        session_id=session_id,
        claimed_grade=assessment.claimed,
        assigned_grade=assessment.assigned,
        outcome=assessment.outcome,
        recommendation=assessment.recommendation,
        levels=assessment.levels,
        thresholds=Thresholds(solved=solved, pass_level=ev.settings.scoring.pass_threshold),
        score=session_score(
            [(outcome.kind, outcome.score) for outcome in outcomes],
            weight_quiz=ev.settings.scoring.weight_quiz,
        ),
        tasks=[_task_result(outcome, solved) for outcome in outcomes],
    )

    async with ev.sessionmaker() as db, db.begin():
        for outcome in outcomes:
            if not outcome.answered:
                continue
            components = {
                "components": outcome.components.model_dump() if outcome.components else None,
                "weights": outcome.weights,
                "quality_items": [item.model_dump() for item in outcome.quality_items],
            }
            await db.execute(
                update(Answer)
                .where(Answer.task_id == outcome.task_id)
                .values(
                    judge=outcome.judge.model_dump(mode="json") if outcome.judge else None,
                    components=components,
                    score=outcome.score,
                )
            )
        await db.execute(
            update(TestSession)
            .where(TestSession.id == session_id)
            .values(
                result=result.model_dump(mode="json"),
                status=SCORED,
                scored_at=datetime.now(UTC),
            )
        )
    await refresh_summary(ev, session.candidate_ref)
    return result


async def session_result(ev: Evaluator, session_id: uuid.UUID) -> SessionResult:
    async with ev.sessionmaker() as db:
        session = await db.get(TestSession, session_id)
    if session is None:
        raise NotFoundError("Сессия не найдена")
    if session.status != SCORED or session.result is None:
        raise ConflictError("Сессия ещё не оценена")
    return SessionResult.model_validate(session.result)


# --- Machine summary -----------------------------------------------------------------------


async def refresh_summary(ev: Evaluator, candidate_ref: str) -> None:
    """Перезаписывает summary кандидата по всем его оценённым сессиям."""
    async with ev.sessionmaker() as db:
        sessions = (
            await db.scalars(
                select(TestSession)
                .where(TestSession.candidate_ref == candidate_ref, TestSession.status == SCORED)
                .order_by(TestSession.scored_at.desc())
            )
        ).all()
    facts = [
        SessionFacts(
            session_id=str(session.id),
            area=session.area_code,
            skills=list(session.skills),
            scored_at=session.scored_at.isoformat() if session.scored_at else "",
            result=session.result or {},
        )
        for session in sessions
    ]
    data = summary_data(candidate_ref, facts)
    try:
        # Summary пишет модель генерации, поэтому и рассуждения у него — как у генерации.
        text = await write_summary(
            ev.llm,
            data,
            model=ev.settings.llm.generator_model,
            reasoning_effort=ev.settings.llm.generator_reasoning_effort,
        )
    except httpx.HTTPError:
        # Summary не должен срывать оценку: при недоступной модели — текст по шаблону.
        logger.warning("Summary по шаблону: модель недоступна", exc_info=True)
        text = fallback_text(data)
    statement = insert(CandidateSummary).values(candidate_ref=candidate_ref, text=text, data=data)
    statement = statement.on_conflict_do_update(
        index_elements=[CandidateSummary.candidate_ref],
        set_={"text": text, "data": data, "updated_at": func.now()},
    )
    async with ev.sessionmaker() as db, db.begin():
        await db.execute(statement)


async def candidate_summary(ev: Evaluator, candidate_ref: str) -> CandidateSummary:
    async with ev.sessionmaker() as db:
        summary = await db.get(CandidateSummary, candidate_ref)
    if summary is None:
        raise NotFoundError("Summary кандидата нет: у него ещё нет оценённых сессий")
    return summary
