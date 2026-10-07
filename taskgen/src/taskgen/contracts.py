"""Контракты между модулями сервиса.

Модули bank, sandbox, generation и evaluation зависят только от этих типов и протоколов,
а не друг от друга. Реализация подставляется при сборке приложения, в тестах — подделка.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

# --- Жизненный цикл сессии ------------------------------------------------------------------


class SessionStatus(StrEnum):
    """Статус test_sessions.status. Первые три ставит генерация, scored — оценка."""

    # Задания ещё создаются; первое уже выдано кандидату.
    GENERATING = "generating"
    READY = "ready"
    # Набор заданий не собран; причина — в test_sessions.failure.
    FAILED = "failed"
    SCORED = "scored"


class TaskStatus(StrEnum):
    """Статус tasks.status. Условие и скрытые данные есть только у ready."""

    PENDING = "pending"
    READY = "ready"
    # Задание не создано: шаблон и его замены не дали экземпляра, сервис недоступен или
    # сессия остановлена из-за другого задания. Причина — в tasks.failure.
    FAILED = "failed"


# --- Шаблон задания ---------------------------------------------------------------------


class TaskKind(StrEnum):
    CODE = "code"
    ESSAY = "essay"
    # Теоретический вопрос в кейсе с выбором одного ответа. Оценивается кодом по ключу и в
    # оценку грейда по уровням не входит.
    QUIZ = "quiz"


class ParamSpec(BaseModel):
    """Параметр экземпляра. Значение выбирает код по seed, а не LLM: сложность задаёт шаблон."""

    type: Literal["int", "float", "choice"]
    min: float | None = None
    max: float | None = None
    choices: list[Any] | None = None


class RubricItem(BaseModel):
    """Атомарный пункт проверки: судья отвечает "есть", "частично" или "нет" и цитирует строки."""

    id: str
    text: str
    weight: float = 1.0
    passing_example: str | None = None
    failing_example: str | None = None


class ScenarioFact(BaseModel):
    """Элемент сценария эссе, который обязан попасть в ситуацию: на него опирается рубрика."""

    id: str
    text: str


class QuizVariant(BaseModel):
    """Вариант кейса тестовой задачки: из его фактов следует ровно один верный ответ.

    Разные варианты одного шаблона ведут к разным верным ответам, поэтому утёкший ответ
    одного экземпляра не подходит к другому.
    """

    id: str
    facts: list[ScenarioFact]
    correct: str
    wrong: list[str]


class TemplateSpec(BaseModel):
    """Разобранный md-шаблон. Формат файла описан в taskgen/TEMPLATE_FORMAT.md."""

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    description: str
    area: str
    grade: str
    skills: list[str]
    kind: TaskKind
    runtime: str | None = None
    difficulty: int = Field(ge=1, le=10)
    expected_minutes: int = Field(ge=1)
    params: dict[str, ParamSpec] = {}
    # Переопределение весов балла из ScoringSettings, ключи совпадают с именами полей.
    scoring: dict[str, float] = {}
    generator_prompt: str
    # Только для kind=code: эталон, генератор тестов и имя функции, которую пишет кандидат.
    entrypoint: str | None = None
    reference_solution: str | None = None
    test_generator: str | None = None
    # Только для kind=essay: вопросы идут в условие дословно, ситуацию пишет LLM по фактам,
    # а запрещённые фразы не должны появиться в ситуации. Фразы действуют и для kind=quiz.
    questions: str | None = None
    scenario_facts: list[ScenarioFact] = []
    forbidden: list[str] = []
    # Только для kind=quiz: вопрос идёт в условие дословно, кейс пишет LLM по фактам варианта,
    # который выбран по seed.
    question: str | None = None
    variants: list[QuizVariant] = []
    # У kind=quiz рубрики нет: ответ сверяется с ключом.
    rubric: list[RubricItem] = []


# --- LLM и эмбеддинги -------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


ReasoningEffort = Literal["none", "minimal", "low", "medium", "high"]

# Способ проверки ситуации эссе; что делает каждый, описано в generation/essay.py.
EssayCheckMethod = Literal["llm", "embeddings", "both"]

# Чем модель подтверждает вердикт; что делает каждый способ, описано в evaluation/evidence.py.
EvidenceMode = Literal["quotes", "spans"]


class LLMClient(Protocol):
    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        json_schema: dict[str, Any] | None = None,
        # None — поле не передаётся, и модель рассуждает так, как настроена по умолчанию.
        reasoning_effort: ReasoningEffort | None = None,
    ) -> str:
        """Текст ответа модели; пустая строка, если модель не вернула content."""
        ...


class Embedder(Protocol):
    model_name: str

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


# --- Песочница --------------------------------------------------------------------------


class RunLimits(BaseModel):
    timeout_ms: int
    memory_bytes: int


class RunRequest(BaseModel):
    runtime: str
    files: dict[str, str]
    stdin: str = ""
    args: list[str] = []
    limits: RunLimits


class RunResult(BaseModel):
    stdout: str
    stderr: str
    exit_code: int | None
    signal: str | None = None
    timed_out: bool = False
    wall_ms: int | None = None
    memory_bytes: int | None = None


class TestCase(BaseModel):
    __test__ = False  # Иначе pytest примет класс за набор тестов.

    input: Any
    expected: Any


class TestRunRequest(BaseModel):
    __test__ = False

    runtime: str
    entrypoint: str
    solution: str
    cases: list[TestCase]
    limits: RunLimits


class CaseOutcome(BaseModel):
    passed: bool
    wall_ms: int | None = None
    error: str | None = None


class TestReport(BaseModel):
    __test__ = False

    outcomes: list[CaseOutcome]
    compile_error: str | None = None
    max_wall_ms: int | None = None
    max_memory_bytes: int | None = None

    @property
    def pass_rate(self) -> float:
        if not self.outcomes:
            return 0.0
        return sum(o.passed for o in self.outcomes) / len(self.outcomes)


class SandboxRunner(Protocol):
    async def run(self, request: RunRequest) -> RunResult: ...

    async def run_tests(self, request: TestRunRequest) -> TestReport: ...


class SolverRecord(BaseModel):
    """Как слепой решатель принял экземпляр: сколько раз решал и сколько черновиков отклонил."""

    passed: bool
    attempts: int
    rejected_drafts: int


class HiddenPayload(BaseModel):
    """Содержимое tasks.hidden_payload: пишет генерация, читает оценка, наружу не уходит.

    Для kind=code обязательны cases, entrypoint и runtime. reference_report — прогон эталона
    на тех же cases в той же песочнице: без него скорость и память не оцениваются.
    scoring — переопределение весов балла из TemplateSpec.scoring.
    """

    cases: list[TestCase] = []
    entrypoint: str | None = None
    runtime: str | None = None
    rubric: list[RubricItem] = []
    reference_report: TestReport | None = None
    scoring: dict[str, float] = {}
    # Для kind=quiz: id верного ответа среди публичных вариантов и вариант кейса шаблона.
    answer_key: str | None = None
    quiz_variant: str | None = None
    # Итог слепого решателя для статистики ложных отказов; None — решатель не запускался.
    solver: SolverRecord | None = None


# --- Оценка -----------------------------------------------------------------------------


class AnalysisReport(BaseModel):
    """Объективные метрики кода. Ключи metrics описаны в рубриках метрик, а не в коде."""

    tool: str
    metrics: dict[str, float] = {}
    findings: list[dict[str, Any]] = []


class Analyzer(Protocol):
    languages: frozenset[str]

    def analyze(self, code: str, language: str) -> AnalysisReport: ...


class RubricVerdict(BaseModel):
    item_id: str
    verdict: Literal["yes", "partial", "no"]
    # Текст доказательства: цитаты судьи или текст участков, на которые он сослался.
    quotes: list[str] = []


class JudgeResult(BaseModel):
    verdicts: list[RubricVerdict]
    score: float = Field(ge=0.0, le=1.0)
    # Разброс между сэмплами судьи; высокий разброс — повод для ручной проверки.
    spread: float = 0.0
    # Замечания проверки: повторы судьи, вердикты, снятые проверкой смысла цитат, и почему
    # она пропущена.
    notes: list[str] = []
    # По всем сэмплам: вердикты yes и partial, сколько из них снято из-за неверного
    # доказательства и сколько раз судью переспросили.
    claimed: int = 0
    unsupported: int = 0
    repairs: int = 0


class JudgeRequest(BaseModel):
    kind: TaskKind
    statement: str
    answer: str
    rubric: list[RubricItem]
    facts: dict[str, Any] = {}


class Judge(Protocol):
    async def judge(self, request: JudgeRequest) -> JudgeResult: ...


class AISignal(BaseModel):
    """Сигнал детектора. Ненадёжен по природе, на балл не влияет, хранится отдельно."""

    detector: str
    probability: float | None = None
    note: str


class AIDetector(Protocol):
    async def detect(self, code: str, language: str) -> AISignal: ...
