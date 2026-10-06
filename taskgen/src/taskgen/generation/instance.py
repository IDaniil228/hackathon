"""Экземпляр задания по шаблону: "сгенерировал — проверил".

Для kind=code порядок такой: параметры по seed, кейсы от генератора шаблона в песочнице,
прогон эталона на этих кейсах. Экземпляр, на котором эталон проходит не все кейсы,
отбрасывается, и берётся следующий seed. Только проверенный экземпляр получает легенду от LLM:
модель пишет текст условия и не влияет ни на параметры, ни на тесты.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, Field

from taskgen.config import GenerationSettings
from taskgen.contracts import (
    ChatMessage,
    HiddenPayload,
    LLMClient,
    ReasoningEffort,
    RunLimits,
    SandboxRunner,
    TaskKind,
    TemplateSpec,
    TestCase,
    TestReport,
    TestRunRequest,
)
from taskgen.evaluation.jsonout import (
    EmptyReplyError,
    StructuredOutputError,
    complete_structured,
)
from taskgen.generation.harness import GeneratorError, generator_request, parse_generator_output
from taskgen.generation.leaks import hidden_leaks, mentions_entrypoint, public_count
from taskgen.generation.params import draw_params, fill_placeholders
from taskgen.sandbox.environment import describe_all, describe_runtime
from taskgen.sandbox.errors import SandboxError

# --- Ошибки; текст сообщения уходит клиенту ------------------------------------------------


class GenerationError(Exception):
    """Экземпляр не создан."""


class TemplateDefectError(GenerationError):
    """Шаблон не даёт проверенного экземпляра: генератор падает или эталон не проходит тесты."""


class SandboxUnavailableError(GenerationError):
    pass


class LLMUnavailableError(GenerationError):
    pass


class ModelOutputError(GenerationError):
    """Модель отвечала, но ни один её вариант условия не прошёл проверки."""


# --- Публичная часть ------------------------------------------------------------------------


class Signature(BaseModel):
    entrypoint: str = Field(description="Имя функции, которую пишет кандидат")
    runtime: str = Field(description="Окружение песочницы, в котором решение проверяется")


class Example(BaseModel):
    input: Any = Field(description="Аргументы функции: массив позиционных или объект именованных")
    expected: Any = Field(description="Ожидаемый результат")


class PublicPayload(BaseModel):
    """То, что видит кандидат помимо текста условия. Хранится в tasks.public_payload."""

    title: str
    signature: Signature | None = Field(default=None, description="Только для заданий с кодом")
    examples: list[Example] = Field(default=[], description="Публичные примеры; остальные скрыты")
    expected_minutes: int = Field(description="Ожидаемое время на задание, минуты")
    environment: str | None = Field(
        default=None,
        # Описание в схеме API собирается той же функцией, что и значение в задании.
        description="Только для заданий с кодом: окружение и доступные библиотеки. Варианты: "
        + "; ".join(f"{name} — «{text}»" for name, text in describe_all().items()),
    )


class Legend(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    statement: str = Field(min_length=1)


LEGEND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Короткий заголовок задания"},
        "statement": {"type": "string", "description": "Полный текст условия в Markdown"},
    },
    "required": ["title", "statement"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    "Ты пишешь задания для технического теста кандидата на ИТ-вакансию. Пиши по-русски, "
    "ясно и без воды. Не подсказывай решение, не приводи код решения и не придумывай "
    "тестовые данные сверх тех, что даны. Верни JSON с полями title (короткий заголовок) "
    "и statement (полный текст условия в Markdown)."
)


@dataclass(frozen=True)
class TaskInstance:
    kind: TaskKind
    statement: str
    params: dict[str, Any]
    seed: int
    public: PublicPayload
    hidden: HiddenPayload
    validated: bool
    generated_by: str

    def public_dump(self) -> dict[str, Any]:
        return self.public.model_dump(mode="json")

    def hidden_dump(self) -> dict[str, Any]:
        return self.hidden.model_dump(mode="json")


@dataclass(frozen=True)
class InstanceDeps:
    llm: LLMClient
    sandbox: SandboxRunner | None
    model: str
    options: GenerationSettings
    limits: RunLimits
    reasoning_effort: ReasoningEffort | None = None


# --- Генерация ------------------------------------------------------------------------------


def attempt_seeds(seed: int, count: int) -> list[int]:
    """Первый seed — заданный, следующие выводятся из него: перебор тоже воспроизводим."""
    rng = random.Random(seed)
    return [seed] + [rng.getrandbits(63) for _ in range(count - 1)]


async def generate_instance(spec: TemplateSpec, seed: int, deps: InstanceDeps) -> TaskInstance:
    if spec.kind == TaskKind.ESSAY:
        return await _essay(spec, seed, deps)
    return await _code(spec, seed, deps)


async def _essay(spec: TemplateSpec, seed: int, deps: InstanceDeps) -> TaskInstance:
    params = draw_params(spec.params, random.Random(seed))
    request = (
        f"Инструкция автора задания:\n{fill_placeholders(spec.generator_prompt, params)}\n\n"
        "Сформулируй по ней один вопрос, на который кандидат отвечает развёрнутым текстом."
    )
    legend = await _write_legend(spec, request, lambda _legend: None, deps)
    return TaskInstance(
        kind=TaskKind.ESSAY,
        statement=legend.statement,
        params=params,
        seed=seed,
        public=PublicPayload(title=legend.title, expected_minutes=spec.expected_minutes),
        hidden=HiddenPayload(rubric=spec.rubric, scoring=spec.scoring),
        # Эталона у вопроса нет: validated означает только успешный прогон эталона.
        validated=False,
        generated_by=deps.model,
    )


async def _code(spec: TemplateSpec, seed: int, deps: InstanceDeps) -> TaskInstance:
    sandbox = deps.sandbox
    if sandbox is None:
        raise SandboxUnavailableError(
            "Песочница не настроена: задания с кодом нельзя проверить эталоном"
        )
    attempts = deps.options.instance_attempts
    last_reason = ""
    for attempt_seed in attempt_seeds(seed, attempts):
        params = draw_params(spec.params, random.Random(attempt_seed))
        try:
            cases = await _generate_cases(sandbox, spec, params, attempt_seed, deps.limits)
        except GeneratorError as error:
            last_reason = str(error)
            continue
        report = await _run_reference(sandbox, spec, cases, deps.limits)
        last_reason = _reference_problem(report, len(cases))
        if not last_reason:
            break
    else:
        raise TemplateDefectError(
            f"Шаблон {spec.name}: эталон не дал проверенного экземпляра за {attempts} "
            f"попыток; последняя причина — {last_reason}"
        )

    shown = public_count(len(cases), deps.options.public_examples)
    public_cases, hidden_cases = cases[:shown], cases[shown:]
    entrypoint = spec.entrypoint or ""
    try:
        environment = describe_runtime(spec.runtime or "")
    except SandboxError as error:
        raise TemplateDefectError(f"Шаблон {spec.name}: {error}") from error
    request = _code_request(spec, params, public_cases, environment)

    def check(legend: Legend) -> str | None:
        if not mentions_entrypoint(legend.statement, entrypoint):
            return f"в условии нет имени функции {entrypoint}"
        if hidden_leaks(legend.statement, public_cases, hidden_cases):
            return "условие содержит данные скрытых тестов; приводи только данные из примеров"
        return None

    legend = await _write_legend(spec, request, check, deps)
    return TaskInstance(
        kind=TaskKind.CODE,
        statement=legend.statement,
        params=params,
        seed=attempt_seed,
        public=PublicPayload(
            title=legend.title,
            signature=Signature(entrypoint=entrypoint, runtime=spec.runtime or ""),
            examples=[Example(input=c.input, expected=c.expected) for c in public_cases],
            expected_minutes=spec.expected_minutes,
            environment=environment,
        ),
        hidden=HiddenPayload(
            cases=cases,
            entrypoint=spec.entrypoint,
            runtime=spec.runtime,
            rubric=spec.rubric,
            reference_report=report,
            scoring=spec.scoring,
        ),
        validated=True,
        generated_by=deps.model,
    )


async def _generate_cases(
    sandbox: SandboxRunner,
    spec: TemplateSpec,
    params: dict[str, Any],
    seed: int,
    limits: RunLimits,
) -> list[TestCase]:
    request = generator_request(spec.test_generator or "", params, seed, limits)
    try:
        result = await sandbox.run(request)
    except httpx.HTTPError as error:
        raise SandboxUnavailableError("Песочница недоступна, повторите позже") from error
    return parse_generator_output(result)


async def _run_reference(
    sandbox: SandboxRunner, spec: TemplateSpec, cases: list[TestCase], limits: RunLimits
) -> TestReport:
    request = TestRunRequest(
        runtime=spec.runtime or "",
        entrypoint=spec.entrypoint or "",
        solution=spec.reference_solution or "",
        cases=cases,
        limits=limits,
    )
    try:
        return await sandbox.run_tests(request)
    except httpx.HTTPError as error:
        raise SandboxUnavailableError("Песочница недоступна, повторите позже") from error


def _reference_problem(report: TestReport, total: int) -> str:
    """Пустая строка, если эталон прошёл все кейсы, иначе причина отказа."""
    if report.compile_error is not None:
        return f"ошибка компиляции эталона: {report.compile_error}"
    if len(report.outcomes) != total:
        return f"песочница вернула {len(report.outcomes)} результатов на {total} кейсов"
    failed = [i for i, outcome in enumerate(report.outcomes, start=1) if not outcome.passed]
    if failed:
        errors = [o.error for o in report.outcomes if o.error]
        detail = f" ({errors[0]})" if errors else ""
        return f"эталон не прошёл кейсы {failed} из {total}{detail}"
    return ""


def _code_request(
    spec: TemplateSpec, params: dict[str, Any], public: list[TestCase], environment: str
) -> str:
    examples = "\n".join(
        f"- input: {json.dumps(c.input, ensure_ascii=False)}; "
        f"expected: {json.dumps(c.expected, ensure_ascii=False)}"
        for c in public
    )
    return (
        f"Инструкция автора задания:\n{fill_placeholders(spec.generator_prompt, params)}\n\n"
        f"Кандидат пишет функцию `{spec.entrypoint}`. {environment}. Не требуй библиотек "
        "сверх этих. Назови функцию в условии ровно так и опиши аргументы и возвращаемое "
        "значение. Аргументы передаются в том порядке, в каком они перечислены в input "
        "примеров.\n\n"
        f"Примеры (input — аргументы функции, expected — результат):\n{examples}\n\n"
        "Других тестовых данных в условии не приводи."
    )


async def _write_legend(
    spec: TemplateSpec, request: str, check: Callable[[Legend], str | None], deps: InstanceDeps
) -> Legend:
    """Легенда от LLM, прошедшая check; check возвращает причину отказа или None."""
    messages = [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(role="user", content=request),
    ]
    reason = ""
    for _ in range(deps.options.legend_attempts):
        try:
            legend = await complete_structured(
                deps.llm,
                messages,
                Legend,
                LEGEND_SCHEMA,
                model=deps.model,
                temperature=deps.options.legend_temperature,
                reasoning_effort=deps.reasoning_effort,
            )
        except httpx.HTTPError as error:
            raise LLMUnavailableError("Модель генерации недоступна, повторите позже") from error
        except EmptyReplyError:
            reason = "модель вернула пустой ответ, возможно, истратив его на рассуждения"
            continue
        except StructuredOutputError:
            reason = "ответ модели не соответствует формату"
            continue
        reason = check(legend) or ""
        if not reason:
            return legend
        # Отказ объясняется модели без самих скрытых значений: она их не видела и не должна.
        messages = [
            *messages[:2],
            ChatMessage(role="user", content=f"Предыдущий вариант отклонён: {reason}. Перепиши."),
        ]
    raise ModelOutputError(
        f"Шаблон {spec.name}: модель не написала корректное условие за "
        f"{deps.options.legend_attempts} попыток; последняя причина — {reason}"
    )
