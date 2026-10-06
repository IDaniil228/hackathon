"""Контракты между модулями сервиса.

Модули bank, sandbox, generation и evaluation зависят только от этих типов и протоколов,
а не друг от друга. Реализация подставляется при сборке приложения, в тестах — подделка.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

# --- Шаблон задания ---------------------------------------------------------------------


class TaskKind(StrEnum):
    CODE = "code"
    ESSAY = "essay"


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
    rubric: list[RubricItem]


# --- LLM и эмбеддинги -------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMClient(Protocol):
    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        json_schema: dict[str, Any] | None = None,
    ) -> str: ...


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
    quotes: list[str] = []


class JudgeResult(BaseModel):
    verdicts: list[RubricVerdict]
    score: float = Field(ge=0.0, le=1.0)
    # Разброс между сэмплами судьи; высокий разброс — повод для ручной проверки.
    spread: float = 0.0


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
