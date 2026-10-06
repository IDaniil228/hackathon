"""Генерация одного экземпляра: параметры по seed, кейсы из песочницы, проверка эталоном."""

from __future__ import annotations

import json
import random

import pytest

from taskgen.config import GenerationSettings
from taskgen.contracts import HiddenPayload, RunLimits, TestReport
from taskgen.generation.essay import EmbeddingCache, essay_questions
from taskgen.generation.instance import (
    InstanceDeps,
    LLMUnavailableError,
    ModelOutputError,
    SandboxUnavailableError,
    TemplateDefectError,
    generate_instance,
)
from taskgen.generation.params import draw_params
from taskgen.sandbox.environment import describe_runtime
from tests.evaluation.fakes import FakeLLM, http_error
from tests.generation.fakes import (
    BagOfWordsEmbedder,
    LocalSandbox,
    call_entrypoint,
    essay_reply,
)
from tests.generation.templates import ENTRYPOINT, code_spec, essay_spec

MODEL = "generator-model"
LIMITS = RunLimits(timeout_ms=5000, memory_bytes=256 * 1024 * 1024)
CLEAN = f"Напишите функцию {ENTRYPOINT}(nums, k): сумма k наибольших чисел."


def legend(statement: str = CLEAN, title: str = "Склад", **extra) -> str:
    return json.dumps({"title": title, "statement": statement, **extra}, ensure_ascii=False)


def deps(
    llm,
    sandbox=None,
    reasoning_effort=None,
    checker_model=None,
    checker_reasoning_effort=None,
    embedder=None,
    **options,
) -> InstanceDeps:
    return InstanceDeps(
        llm=llm,
        sandbox=sandbox,
        model=MODEL,
        options=GenerationSettings(**options),
        limits=LIMITS,
        reasoning_effort=reasoning_effort,
        checker_model=checker_model,
        checker_reasoning_effort=checker_reasoning_effort,
        embedder=embedder,
        embedding_cache=EmbeddingCache() if embedder is not None else None,
    )


def prompt_of(call) -> str:
    return "\n".join(message.content for message in call["messages"])


async def test_code_instance_is_validated_by_reference():
    spec = code_spec()
    sandbox = LocalSandbox()
    llm = FakeLLM(lambda _m, _s: legend())
    instance = await generate_instance(spec, 1234, deps(llm, sandbox))

    assert instance.seed == 1234
    assert instance.params == draw_params(spec.params, random.Random(1234))
    assert instance.validated is True
    assert instance.statement == CLEAN
    assert instance.generated_by == MODEL

    # Кейсы пришли из песочницы, а эталон прогнан на них в окружении шаблона.
    [run] = sandbox.runs
    assert json.loads(run.stdin)["seed"] == 1234
    [test_run] = sandbox.test_runs
    assert test_run.runtime == "python"
    assert test_run.entrypoint == ENTRYPOINT
    assert test_run.solution == spec.reference_solution
    hidden = instance.hidden
    assert test_run.cases == hidden.cases
    assert len(hidden.cases) == instance.params["count"]
    for case in hidden.cases:
        assert call_entrypoint(spec.reference_solution, ENTRYPOINT, case.input) == case.expected

    # В скрытые данные пишутся ровно поля HiddenPayload, и их понимает оценка.
    assert set(instance.hidden_dump()) == set(HiddenPayload.model_fields)
    assert HiddenPayload.model_validate(instance.hidden_dump()) == hidden
    assert hidden.entrypoint == ENTRYPOINT and hidden.runtime == "python"
    assert hidden.rubric == spec.rubric
    assert hidden.reference_report is not None
    assert hidden.reference_report.pass_rate == 1.0

    public = instance.public
    assert public.title == "Склад"
    assert public.signature.entrypoint == ENTRYPOINT
    assert public.signature.runtime == "python"
    assert public.expected_minutes == spec.expected_minutes
    assert [(e.input, e.expected) for e in public.examples] == [
        (c.input, c.expected) for c in hidden.cases[:2]
    ]
    # Окружение кандидат видит в том виде, в каком его описывает песочница.
    assert public.environment == describe_runtime("python")


async def test_legend_prompt_names_environment():
    llm = FakeLLM(lambda _m, _s: legend())
    await generate_instance(code_spec(), 1, deps(llm, LocalSandbox()))
    assert describe_runtime("python") in prompt_of(llm.calls[0])


async def test_prompt_has_params_signature_and_only_public_examples():
    spec = code_spec()
    llm = FakeLLM(lambda _m, _s: legend())
    instance = await generate_instance(spec, 99, deps(llm, LocalSandbox()))
    [call] = llm.calls
    prompt = prompt_of(call)
    assert call["model"] == MODEL
    assert f"про {instance.params['topic']}" in prompt
    assert f"из {instance.params['n']} чисел" in prompt
    assert "{k}" in prompt  # Экранированная скобка шаблона.
    assert ENTRYPOINT in prompt
    for example in instance.public.examples:
        assert json.dumps(example.input, ensure_ascii=False) in prompt
    for case in instance.hidden.cases[2:]:
        assert json.dumps(case.input[0]) not in prompt
        assert str(case.expected) not in prompt


async def test_same_seed_reproduces_instance():
    spec = code_spec()
    llm = FakeLLM(lambda _m, _s: legend())
    first = await generate_instance(spec, 7, deps(llm, LocalSandbox()))
    again = await generate_instance(spec, 7, deps(llm, LocalSandbox()))
    other = await generate_instance(spec, 8, deps(llm, LocalSandbox()))
    assert (first.params, first.hidden.cases) == (again.params, again.hidden.cases)
    assert first.hidden.cases != other.hidden.cases


async def test_params_come_from_seed_not_from_model():
    spec = code_spec()
    llm = FakeLLM(lambda _m, _s: legend(params={"n": 1000, "count": 1, "topic": "LLM"}))
    instance = await generate_instance(spec, 5, deps(llm, LocalSandbox()))
    assert instance.params == draw_params(spec.params, random.Random(5))


def _fail_first(times: int):
    calls = {"n": 0}

    def tamper(_request, report: TestReport) -> TestReport:
        calls["n"] += 1
        if calls["n"] <= times:
            report.outcomes[0].passed = False
        return report

    return tamper


async def test_failed_reference_discards_instance_and_takes_new_seed():
    spec = code_spec()
    sandbox = LocalSandbox()
    sandbox.tamper = _fail_first(1)
    llm = FakeLLM(lambda _m, _s: legend())
    instance = await generate_instance(spec, 1234, deps(llm, sandbox))

    assert len(sandbox.test_runs) == 2
    assert instance.seed != 1234
    assert instance.validated is True
    # Сохранённый seed воспроизводит именно выданный экземпляр, а не отброшенный.
    assert instance.params == draw_params(spec.params, random.Random(instance.seed))
    assert json.loads(sandbox.runs[-1].stdin)["seed"] == instance.seed
    assert instance.hidden.cases == sandbox.test_runs[-1].cases


async def test_reference_failing_every_attempt_is_template_defect():
    spec = code_spec()
    sandbox = LocalSandbox()
    sandbox.tamper = _fail_first(100)
    llm = FakeLLM(lambda _m, _s: legend())
    with pytest.raises(TemplateDefectError, match=r"эталон.*3") as caught:
        await generate_instance(spec, 1, deps(llm, sandbox, instance_attempts=3))
    assert spec.name in str(caught.value)
    assert len(sandbox.test_runs) == 3
    # Легенда пишется только для проверенного экземпляра.
    assert llm.calls == []


async def test_reference_compile_error_is_reported():
    spec = code_spec()
    sandbox = LocalSandbox()

    def broken(_request, _report):
        return TestReport(outcomes=[], compile_error="SyntaxError: строка 1")

    sandbox.tamper = broken
    with pytest.raises(TemplateDefectError, match="SyntaxError"):
        await generate_instance(spec, 1, deps(FakeLLM([]), sandbox, instance_attempts=2))


async def test_broken_generator_is_template_defect():
    generator = "```python\ndef generate(params, rng):\n    raise RuntimeError('нет данных')\n```"
    spec = code_spec(sections={"Тесты": generator})
    sandbox = LocalSandbox()
    with pytest.raises(TemplateDefectError, match="нет данных"):
        await generate_instance(spec, 1, deps(FakeLLM([]), sandbox, instance_attempts=2))
    assert len(sandbox.runs) == 2
    assert sandbox.test_runs == []


async def test_sandbox_missing_or_down():
    with pytest.raises(SandboxUnavailableError):
        await generate_instance(code_spec(), 1, deps(FakeLLM([]), None))
    sandbox = LocalSandbox()
    sandbox.down = True
    with pytest.raises(SandboxUnavailableError):
        await generate_instance(code_spec(), 1, deps(FakeLLM([]), sandbox))


async def test_leaking_legend_is_rewritten():
    spec = code_spec()
    sandbox = LocalSandbox()
    seen: list[str] = []

    def responder(messages, _schema):
        seen.append(prompt_of({"messages": messages}))
        if len(seen) == 1:
            hidden = sandbox.test_runs[-1].cases[-1]
            return legend(f"{CLEAN} Проверьте на {json.dumps(hidden.input[0])}.")
        return legend()

    instance = await generate_instance(spec, 3, deps(FakeLLM(responder), sandbox))
    assert len(seen) == 2
    assert instance.statement == CLEAN
    assert "скрытых тестов" in seen[1]


async def test_legend_without_entrypoint_is_rewritten():
    replies = [legend("Посчитайте сумму наибольших чисел."), legend()]
    llm = FakeLLM(replies)
    instance = await generate_instance(code_spec(), 3, deps(llm, LocalSandbox()))
    assert len(llm.calls) == 2
    assert instance.statement == CLEAN
    assert ENTRYPOINT in prompt_of(llm.calls[1])


async def test_cut_off_legend_is_rewritten():
    # Реальный случай: Saiga закрыла строку JSON посреди примера, и условие оборвалось.
    cut = legend(f"{CLEAN}\n\n```python\ninput = [[")
    llm = FakeLLM([cut, legend()])
    instance = await generate_instance(code_spec(), 3, deps(llm, LocalSandbox()))
    assert len(llm.calls) == 2
    assert instance.statement == CLEAN
    assert "оборвано" in prompt_of(llm.calls[1])


async def test_legend_that_never_passes_checks_is_model_error():
    llm = FakeLLM(lambda _m, _s: legend("Без имени функции."))
    with pytest.raises(ModelOutputError, match="нет имени функции"):
        await generate_instance(code_spec(), 3, deps(llm, LocalSandbox(), legend_attempts=2))
    assert len(llm.calls) == 2


async def test_unparsable_legend_is_retried_then_model_error():
    llm = FakeLLM(lambda _m, _s: "не JSON")
    with pytest.raises(ModelOutputError):
        await generate_instance(code_spec(), 3, deps(llm, LocalSandbox(), legend_attempts=2))
    # complete_structured делает два вызова на попытку: со схемой и без неё.
    assert len(llm.calls) == 4


async def test_llm_down_is_unavailable():
    def responder(_m, _s):
        raise http_error(503)

    with pytest.raises(LLMUnavailableError):
        await generate_instance(code_spec(), 3, deps(FakeLLM(responder), LocalSandbox()))


async def test_essay_is_checked_situation_plus_verbatim_questions():
    spec = essay_spec()
    sandbox = LocalSandbox()
    llm = FakeLLM(essay_reply)
    instance = await generate_instance(spec, 11, deps(llm, sandbox, checker_model="checker"))

    assert sandbox.runs == [] and sandbox.test_runs == []
    assert instance.params == draw_params(spec.params, random.Random(11))
    scenario, check = llm.calls
    assert f"про {instance.params['topic']}" in prompt_of(scenario)
    assert scenario["model"] == MODEL and check["model"] == "checker"
    # Вопросы автора идут в условие дословно, после ситуации, и писатель их не видит.
    questions = essay_questions(spec, instance.params)
    assert instance.statement.endswith(questions)
    assert questions not in prompt_of(scenario)
    assert all(fact.text in instance.statement for fact in spec.scenario_facts)
    assert instance.public.title == "Ситуация"
    assert instance.hidden.cases == []
    assert instance.hidden.rubric == spec.rubric
    assert instance.hidden.reference_report is None
    # Эталона у вопроса нет, поэтому экземпляр не помечается проверенным.
    assert instance.validated is False
    assert instance.public.signature is None
    assert instance.public.examples == []
    assert instance.public.environment is None


async def test_essay_situation_without_facts_is_retried_then_model_error():
    llm = FakeLLM(lambda m, s: essay_reply(m, s, keep_facts=False))
    with pytest.raises(ModelOutputError, match="нет фактов"):
        await generate_instance(essay_spec(), 1, deps(llm, None, legend_attempts=2))
    # Две попытки: ситуация и проверка в каждой; вторая ситуация знает причину отказа.
    assert len(llm.calls) == 4
    assert "Предыдущий вариант отклонён" in prompt_of(llm.calls[2])


async def test_essay_checker_without_own_model_uses_generator_model_and_efforts():
    llm = FakeLLM(essay_reply)
    await generate_instance(
        essay_spec(),
        1,
        deps(llm, None, reasoning_effort="none", checker_reasoning_effort="low"),
    )
    scenario, check = llm.calls
    assert scenario["model"] == check["model"] == MODEL
    assert (scenario["reasoning_effort"], check["reasoning_effort"]) == ("none", "low")


async def test_essay_check_method_comes_from_generation_settings():
    llm = FakeLLM(essay_reply)
    embedder = BagOfWordsEmbedder()
    settings = {"essay_check_method": "embeddings", "essay_fact_threshold": 0.9}

    instance = await generate_instance(
        essay_spec(), 1, deps(llm, None, embedder=embedder, **settings)
    )

    # Ситуацию проверили эмбеддинги: в LLM ушёл только запрос ситуации.
    [scenario] = llm.calls
    assert "situation" in scenario["json_schema"]["properties"]
    assert embedder.calls and instance.statement


async def test_essay_embeddings_without_embedder_are_checked_by_llm():
    llm = FakeLLM(essay_reply)

    await generate_instance(essay_spec(), 1, deps(llm, None, essay_check_method="embeddings"))

    _scenario, check = llm.calls
    assert "facts" in check["json_schema"]["properties"]


async def test_essay_with_llm_down_is_unavailable():
    def responder(_m, _s):
        raise http_error(503)

    with pytest.raises(LLMUnavailableError):
        await generate_instance(essay_spec(), 1, deps(FakeLLM(responder), None))


async def test_empty_reply_after_reasoning_is_retried_within_legend_attempts():
    llm = FakeLLM(["", legend()])
    instance = await generate_instance(code_spec(), 3, deps(llm, LocalSandbox()))
    assert instance.statement == CLEAN
    assert len(llm.calls) == 2


async def test_only_empty_replies_are_model_error_not_crash():
    llm = FakeLLM(lambda _m, _s: "")
    with pytest.raises(ModelOutputError, match="пуст"):
        await generate_instance(code_spec(), 3, deps(llm, LocalSandbox(), legend_attempts=2))
    assert len(llm.calls) == 2


async def test_reasoning_effort_reaches_legend_call():
    llm = FakeLLM(lambda _m, _s: legend())
    await generate_instance(code_spec(), 3, deps(llm, LocalSandbox(), reasoning_effort="none"))
    assert {call["reasoning_effort"] for call in llm.calls} == {"none"}
