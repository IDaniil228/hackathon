"""Проверка шаблона с кодом в песочнице: один код для импорта, шлюза банка и генерации.

Шаблон проверяется так, как его исполнит генерация экземпляра: параметры по seed, кейсы от
генератора в песочнице, прогон решений на этих кейсах. На каждом seed из настроек:

- генератор даёт не меньше MIN_CASES кейсов, не все с одним ответом, входы — массивы
  аргументов, и все входы помещаются в один запрос к Piston;
- каждое верное решение ('### id +') проходит все кейсы;
- каждое неверное решение ('### id -') и решения-константы, которые не смотрят на вход,
  не решены даже при идеальных рубрике, качестве и скорости.

Генерация экземпляра вызывает отсюда generate_cases, run_solution и reference_problem:
входы зависят от seed экземпляра, поэтому прогон первого верного решения при генерации
остаётся страховкой на этом одном seed.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from taskgen.bank.parser import TemplateIssue
from taskgen.config import ImportCheckSettings, ScoringSettings, Settings
from taskgen.contracts import (
    RunLimits,
    SandboxRunner,
    TaskKind,
    TemplateSpec,
    TestCase,
    TestReport,
    TestRunRequest,
)
from taskgen.evaluation.grading import is_solved
from taskgen.evaluation.scoring import (
    Components,
    code_task_score,
    failed_required_groups,
    resolve_weights,
    weighted_pass_rate,
)
from taskgen.generation.harness import GeneratorError, generator_request, parse_generator_output
from taskgen.generation.params import draw_params
from taskgen.sandbox.errors import SandboxError, SandboxServiceError

# Меньше кейсов не хватает на публичные примеры, граничные случаи и случайные входы.
MIN_CASES = 6
# Piston принимает тело запроса не больше 100 КиБ, а входы всех кейсов уходят одним
# запросом вместе с решением. Остаток оставлен под код кандидата.
MAX_INPUT_BYTES = 64 * 1024


# --- Запуски в песочнице; их же вызывает генерация экземпляра --------------------------------


async def generate_cases(
    sandbox: SandboxRunner,
    spec: TemplateSpec,
    params: dict[str, Any],
    seed: int,
    limits: RunLimits,
) -> list[TestCase]:
    """Кейсы генератора шаблона; GeneratorError — генератор упал или вернул не то."""
    request = generator_request(spec.test_generator or "", params, seed, limits)
    result = await _call(sandbox.run(request))
    return parse_generator_output(result, spec.case_groups)


async def run_solution(
    sandbox: SandboxRunner,
    spec: TemplateSpec,
    solution: str,
    cases: list[TestCase],
    limits: RunLimits,
) -> TestReport:
    request = solution_request(spec, solution, cases, limits)
    return await _call(sandbox.run_tests(request))


def solution_request(
    spec: TemplateSpec, solution: str, cases: list[TestCase], limits: RunLimits
) -> TestRunRequest:
    """Запрос прогона решения на кейсах так, как задание с кодом шаблона его проверяет."""
    if spec.io is None or spec.check is None:
        raise SandboxError(f"шаблон {spec.name} без вида io или режима проверки")
    return TestRunRequest(
        runtime=spec.runtime or "",
        io=spec.io,
        entrypoint=spec.entrypoint,
        solution=solution,
        cases=cases,
        check=spec.check,
        limits=limits,
    )


async def _call[T](call: Any) -> T:
    """Сетевая ошибка подделки или клиента — тот же отказ песочницы, что SandboxServiceError."""
    try:
        return await call
    except httpx.HTTPError as error:
        raise SandboxServiceError(f"песочница недоступна: {error!r}") from error


def reference_problem(report: TestReport, total: int) -> str:
    """Пустая строка, если решение прошло все кейсы, иначе причина с номерами кейсов."""
    if report.compile_error is not None:
        return f"ошибка компиляции: {report.compile_error}"
    if len(report.outcomes) != total:
        return f"песочница вернула {len(report.outcomes)} результатов на {total} кейсов"
    failed = [i for i, outcome in enumerate(report.outcomes, start=1) if not outcome.passed]
    if failed:
        errors = [o.error for o in report.outcomes if o.error]
        detail = f" ({errors[0]})" if errors else ""
        return f"кейсы {failed} из {total} не пройдены{detail}"
    return ""


# --- Правила без песочницы ------------------------------------------------------------------


def static_problems(spec: TemplateSpec, source: str | None = None) -> list[TemplateIssue]:
    """Правила банка для шаблона с кодом, которым песочница не нужна."""
    if spec.kind is not TaskKind.CODE:
        return []
    # Без групп все кейсы равноценны, и решение, которое проходит частые простые кейсы,
    # набирает балл решённого. Гейт нужен, потому что одна ошибка роняет один-два кейса
    # из десятков, и никакой вес не опустит такой балл ниже порога.
    if not any(group.required for group in spec.case_groups.values()):
        return [
            TemplateIssue(
                "case_groups",
                "нужна хотя бы одна группа кейсов с required: true: без гейта неверное "
                "решение, которое падает на одном кейсе из многих, считается решённым",
                source,
            )
        ]
    return []


def case_problems(cases: list[TestCase]) -> list[str]:
    problems = []
    if len(cases) < MIN_CASES:
        problems.append(f"генератор дал {len(cases)} кейсов, нужно не меньше {MIN_CASES}")
    # Объект именованных аргументов сервис принимает, но обвязка C++ понимает только массив.
    objects = [i for i, case in enumerate(cases, start=1) if not isinstance(case.input, list)]
    if objects:
        problems.append(f"входы кейсов {objects} — не массив аргументов функции")
    distinct = {json.dumps(case.expected, sort_keys=True) for case in cases}
    if len(distinct) < 2:
        problems.append("все кейсы ждут один ответ: его угадает решение-константа")
    size = len(json.dumps([case.input for case in cases], ensure_ascii=False).encode())
    if size > MAX_INPUT_BYTES:
        problems.append(f"входы кейсов занимают {size} байт, допустимо {MAX_INPUT_BYTES}")
    return problems


def constant_solutions(spec: TemplateSpec, cases: list[TestCase]) -> dict[str, str]:
    """Решения, которые возвращают константу и не смотрят на вход; ключ — эта константа."""
    entry = spec.entrypoint or ""
    if spec.runtime == "cpp":
        signature = re.search(
            rf"^([^\n;{{}}()]+?)\b{entry}\s*\(([^)]*)\)\s*\{{",
            spec.reference_solution or "",
            re.MULTILINE,
        )
        if signature is None:
            return {}
        result, args = signature.group(1).strip(), signature.group(2)
        return {"{}": f"{result} {entry}({args}) {{ return {{}}; }}\n"}
    first = cases[0].expected
    default = type(first)() if first is not None else 0
    return {
        json.dumps(value, ensure_ascii=False): (
            f"import json\n\n\ndef {entry}(*args, **kwargs):\n"
            f"    return json.loads({json.dumps(json.dumps(value))})\n"
        )
        for value in (default, first)
    }


def solved_at_best(
    spec: TemplateSpec, cases: list[TestCase], report: TestReport, scoring: ScoringSettings
) -> bool:
    """Решено ли задание, если судья, анализаторы и замер скорости поставят решению 1.

    Это верхняя граница балла: неверное решение, которое не решено даже так, не станет
    решённым ни при каком судье.
    """
    components = Components(
        tests=weighted_pass_rate(report, cases, spec.case_groups),
        rubric=1.0,
        quality=1.0,
        performance=1.0,
    )
    score = code_task_score(components, resolve_weights(scoring, spec.scoring))
    failed = failed_required_groups(
        report, cases, spec.case_groups, default=scoring.required_case_groups
    )
    return is_solved(score, scoring.solved_threshold, required_passed=not failed)


# --- Проверка шаблона -----------------------------------------------------------------------


@dataclass
class SeedCheck:
    """Итог одного seed: что дал генератор и как прошли решения; problems пуст — seed чист."""

    seed: int
    params: dict[str, Any]
    cases: list[TestCase] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    # Имя решения → прошло все кейсы (верное) или поймано тестами (неверное).
    correct: dict[str, bool] = field(default_factory=dict)
    caught: dict[str, bool] = field(default_factory=dict)


class TemplateValidator:
    """Проверяет шаблоны с кодом в песочнице; шаблонам без кода песочница не нужна.

    Отказ самой песочницы — SandboxServiceError на весь набор: шаблон из-за него не
    отклоняется и не проходит молча. Ошибка шаблона — TemplateIssue с seed и причиной.
    """

    def __init__(
        self,
        sandbox: SandboxRunner | None,
        *,
        check: ImportCheckSettings,
        limits: RunLimits,
        scoring: ScoringSettings,
    ) -> None:
        self.sandbox = sandbox
        self.options = check
        self.limits = limits
        self.scoring = scoring

    @classmethod
    def from_settings(cls, sandbox: SandboxRunner | None, settings: Settings) -> TemplateValidator:
        return cls(
            sandbox,
            check=settings.import_check,
            limits=RunLimits(
                timeout_ms=settings.sandbox.run_timeout_ms,
                memory_bytes=settings.sandbox.run_memory_limit_bytes,
            ),
            scoring=settings.scoring,
        )

    async def check(self, templates: list[tuple[str, TemplateSpec]]) -> list[TemplateIssue]:
        """Проблемы всех шаблонов набора; ошибка одного шаблона не останавливает остальные."""
        issues = [i for source, spec in templates for i in static_problems(spec, source)]
        code = [(source, spec) for source, spec in templates if spec.kind is TaskKind.CODE]
        if not code:
            return issues
        if self.sandbox is None:
            raise SandboxServiceError("песочница не настроена: шаблон с кодом нельзя проверить")
        limit = asyncio.Semaphore(self.options.concurrency)

        async def one(source: str, spec: TemplateSpec) -> list[TemplateIssue]:
            async with limit:
                return await self.check_template(spec, source)

        try:
            async with asyncio.TaskGroup() as group:
                tasks = [group.create_task(one(source, spec)) for source, spec in code]
        except* SandboxServiceError as failure:
            raise failure.exceptions[0] from None
        return issues + [issue for task in tasks for issue in task.result()]

    async def check_template(self, spec: TemplateSpec, source: str) -> list[TemplateIssue]:
        issues = []
        for seed in range(self.options.seeds):
            result = await self.check_seed(spec, seed)
            issues += [TemplateIssue(f"seed {seed}", p, source) for p in result.problems]
        return issues

    async def check_seed(self, spec: TemplateSpec, seed: int) -> SeedCheck:
        sandbox = self.sandbox
        if sandbox is None:
            raise SandboxServiceError("песочница не настроена: шаблон с кодом нельзя проверить")
        params = draw_params(spec.params, random.Random(seed))
        result = SeedCheck(seed, params)
        try:
            result.cases = await generate_cases(sandbox, spec, params, seed, self.limits)
        except GeneratorError as error:
            result.problems.append(f"{error}; параметры {params}")
            return result
        cases = result.cases
        result.problems += case_problems(cases)
        try:
            await self._run_correct(spec, result)
            await self._run_wrong(spec, result)
        except SandboxServiceError:
            raise
        except SandboxError as error:
            # Неизвестное окружение или имя функции, которое обвязка не принимает.
            result.problems.append(f"песочница не запустила решение: {error}")
        return result

    async def _run_correct(self, spec: TemplateSpec, result: SeedCheck) -> None:
        for solution in spec.solutions:
            if not solution.correct:
                continue
            report = await self._run(spec, solution.code, result.cases)
            problem = reference_problem(report, len(result.cases))
            if not problem and not solved_at_best(spec, result.cases, report, self.scoring):
                problem = "все кейсы пройдены, но задание не решено: проверьте веса балла"
            result.correct[solution.id] = not problem
            if problem:
                result.problems.append(f"верное решение {solution.id!r}: {problem}")

    async def _run_wrong(self, spec: TemplateSpec, result: SeedCheck) -> None:
        wrong = {
            f"неверное решение {s.id!r}" + (f" ({s.description})" if s.description else ""): s.code
            for s in spec.solutions
            if not s.correct
        }
        constants = constant_solutions(spec, result.cases)
        if not constants:
            result.problems.append(
                f"в первом верном решении не найдено определение {spec.entrypoint}: "
                "решение-константу для проверки тестов не построить"
            )
        wrong |= {f"решение-константа {value}": code for value, code in constants.items()}
        for name, code in wrong.items():
            report = await self._run(spec, code, result.cases)
            if report.compile_error is not None:
                # Решение, которое не компилируется, проваливает всё и ничего не проверяет.
                result.caught[name] = False
                result.problems.append(f"{name} не компилируется: {report.compile_error}")
                continue
            caught = not solved_at_best(spec, result.cases, report, self.scoring)
            result.caught[name] = caught
            if not caught:
                result.problems.append(
                    f"{name} засчитано решённым даже при идеальных рубрике, качестве и "
                    "скорости: тесты его не ловят"
                )

    async def _run(self, spec: TemplateSpec, code: str, cases: list[TestCase]) -> TestReport:
        assert self.sandbox is not None
        return await run_solution(self.sandbox, spec, code, cases, self.limits)
