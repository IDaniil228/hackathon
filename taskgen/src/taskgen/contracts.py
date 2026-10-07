"""Контракты между модулями сервиса.

Модули bank, sandbox, generation и evaluation зависят только от этих типов и протоколов,
а не друг от друга. Реализация подставляется при сборке приложения, в тестах — подделка.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

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


class Solution(BaseModel):
    """Размеченное решение шаблона с кодом; кандидат и LLM решений не видят.

    correct=True — решение проходит все кейсы, correct=False — заведомо неверное: тесты
    шаблона обязаны его поймать. description — необязательное пояснение автора к ошибке.
    """

    id: str
    correct: bool
    code: str
    description: str = ""


class CaseGroup(BaseModel):
    """Группа тестовых кейсов задания с кодом: её доля в балле за тесты и гейт решённости.

    weight — доля группы целиком, а не одного кейса: число кейсов в группе вес не меняет.
    required — задание решено, только если пройдены все кейсы группы; None — решает
    настройка ScoringSettings.required_case_groups.
    """

    model_config = ConfigDict(extra="forbid")

    weight: float = Field(gt=0)
    required: bool | None = None


CaseGroupName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]*$")]


class IOKind(StrEnum):
    """Как решение задания с кодом получает вход и отдаёт ответ."""

    # Кандидат пишет функцию; драйвер языка вызывает её на аргументах кейса.
    FUNCTION = "function"
    # Кандидат пишет программу: вход в stdin и файлах, ответ — её stdout.
    STDIO = "stdio"


# Как ответ решения сверяется с ожидаемым; смысл режимов описан в taskgen/sandbox/checkers.py.
CheckMode = Literal["tokens", "exact", "float", "json", "checker"]


class Checker(BaseModel):
    """Свой проверяльщик шаблона: программа, которая исполняется в песочнице.

    Контракт программы описан в taskgen/sandbox/cases.py. Кандидат проверяльщика не видит.
    """

    runtime: str
    code: str


class OutputCheck(BaseModel):
    """Режим проверки ответа; checker задан только при mode=checker."""

    mode: CheckMode
    checker: Checker | None = None


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
    # Только для kind=code: генератор тестов и имя функции, которую пишет кандидат.
    entrypoint: str | None = None
    test_generator: str | None = None
    # Только для kind=code: группы, которые генератор тестов ставит кейсам в поле group.
    # Пусто — все кейсы равноценны.
    case_groups: dict[CaseGroupName, CaseGroup] = {}
    # Только для kind=code: верные и заведомо неверные решения в порядке шаблона.
    solutions: list[Solution] = []
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

    @property
    def reference_solution(self) -> str | None:
        """Первое верное решение: его прогон проверяет экземпляр и задаёт мерило скорости."""
        return next((s.code for s in self.solutions if s.correct), None)


# --- LLM и эмбеддинги -------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


ReasoningEffort = Literal["none", "minimal", "low", "medium", "high"]

# Как провайдер принимает просьбу об ответе по схеме; смысл значений описан у поля
# structured_output в config.py.
StructuredOutput = Literal["json_schema", "json_object", "text"]

# Способ проверки ситуации эссе; что делает каждый, описано в generation/essay.py.
EssayCheckMethod = Literal["llm", "embeddings", "both"]


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
    # Вывод превысил лимит песочницы, и процесс остановлен.
    output_limited: bool = False
    wall_ms: int | None = None
    memory_bytes: int | None = None
    # Сообщение компилятора; не None — программа не собралась и не запускалась.
    compile_error: str | None = None


class TestCase(BaseModel):
    """Кейс задания с кодом. У io=function вход — input, у io=stdio — stdin и files.

    expected у io=function — значение JSON, у io=stdio — ожидаемый вывод строкой.
    """

    __test__ = False  # Иначе pytest примет класс за набор тестов.

    # Аргументы функции: массив позиционных или объект именованных.
    input: Any = None
    stdin: str | None = None
    # Файлы, которые лежат рядом с программой: имя → содержимое.
    files: dict[str, str] = {}
    expected: Any
    # Группа из TemplateSpec.case_groups; None — шаблон групп не объявил.
    group: str | None = None


class TestRunRequest(BaseModel):
    __test__ = False

    runtime: str
    io: IOKind
    # Только для io=function: имя функции кандидата.
    entrypoint: str | None = None
    solution: str
    cases: list[TestCase]
    check: OutputCheck
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
    scoring — переопределение весов балла из TemplateSpec.scoring, case_groups — группы
    кейсов из TemplateSpec.case_groups: веса групп кандидат не видит.
    """

    cases: list[TestCase] = []
    entrypoint: str | None = None
    runtime: str | None = None
    rubric: list[RubricItem] = []
    reference_report: TestReport | None = None
    scoring: dict[str, float] = {}
    case_groups: dict[str, CaseGroup] = {}
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
    quotes: list[str] = []


class JudgeResult(BaseModel):
    verdicts: list[RubricVerdict]
    score: float = Field(ge=0.0, le=1.0)
    # Разброс между сэмплами судьи; высокий разброс — повод для ручной проверки.
    spread: float = 0.0
    # Замечания проверки: какие вердикты сняты проверкой смысла цитат и почему она пропущена.
    notes: list[str] = []


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
