"""Экземпляр тестовой задачки: вариант по seed, кейс от LLM, ответы дословно и вперемешку."""

from __future__ import annotations

import json
import random
from collections import Counter

import pytest

from taskgen.config import EvidenceSettings, GenerationSettings
from taskgen.contracts import RunLimits, TaskKind
from taskgen.generation.instance import (
    InstanceDeps,
    LLMUnavailableError,
    ModelOutputError,
    generate_instance,
)
from taskgen.generation.params import draw_params, fill_placeholders
from taskgen.generation.quiz import choose_variant
from tests.bank.factory import QUIZ_VARIANTS
from tests.evaluation.fakes import FakeLLM, http_error
from tests.generation.fakes import essay_reply, quiz_solver_reply
from tests.generation.templates import quiz_spec

MODEL = "generator-model"
SOLVER = "solver-model"
LIMITS = RunLimits(timeout_ms=5000, memory_bytes=256 * 1024 * 1024)


def deps(llm, evidence=None, **options) -> InstanceDeps:
    return InstanceDeps(
        llm=llm,
        sandbox=None,
        model=MODEL,
        options=GenerationSettings(**options),
        evidence=evidence or EvidenceSettings(),
        limits=LIMITS,
        solver_model=SOLVER,
    )


def honest(spec, seed):
    """Модель, которая пишет кейс по фактам и решает задачку верно."""
    variant = choose_variant(spec, seed)

    def respond(messages, schema):
        return quiz_solver_reply(messages, schema, pick=variant.correct) or essay_reply(
            messages, schema
        )

    return respond


async def test_quiz_is_case_then_question_with_shuffled_options_and_hidden_key():
    spec = quiz_spec()
    seed = 77
    llm = FakeLLM(honest(spec, seed))
    instance = await generate_instance(spec, seed, deps(llm))

    variant = choose_variant(spec, seed)
    params = draw_params(spec.params, random.Random(seed))
    assert instance.kind is TaskKind.QUIZ
    assert instance.params == params
    # Кейс написан по фактам выбранного варианта, вопрос идёт дословно после него.
    question = fill_placeholders(spec.question, params)
    assert instance.statement.endswith(question)
    for fact in variant.facts:
        assert fill_placeholders(fact.text, params) in instance.statement

    options = instance.public.options
    assert sorted(option.text for option in options) == sorted([variant.correct, *variant.wrong])
    assert [option.id for option in options] == ["a", "b", "c", "d"]
    key = instance.hidden.answer_key
    assert next(o.text for o in options if o.id == key) == variant.correct
    assert instance.hidden.quiz_variant == variant.id
    # В публичной части нет ни ключа, ни варианта кейса, ни признака верного ответа.
    public = json.dumps(instance.public_dump(), ensure_ascii=False)
    for secret in ("answer_key", "quiz_variant", variant.id, "correct"):
        assert secret not in public
    # Решатель выключен по умолчанию: два вызова — кейс и проверка фактов.
    assert len(llm.calls) == 2
    assert instance.hidden.solver is None


@pytest.mark.parametrize("mode", ["quotes", "spans"])
async def test_case_checker_evidence_comes_from_evidence_settings(mode):
    spec = quiz_spec()
    llm = FakeLLM(honest(spec, 77))

    instance = await generate_instance(spec, 77, deps(llm, evidence=EvidenceSettings(mode=mode)))

    _case, check = llm.calls
    item = check["json_schema"]["properties"]["facts"]["items"]["properties"]
    assert mode in item
    assert instance.statement


async def test_variant_and_option_order_come_from_seed():
    spec = quiz_spec()
    variants: Counter[str] = Counter()
    key_positions: Counter[str] = Counter()
    for seed in range(40):
        instance = await generate_instance(spec, seed, deps(FakeLLM(honest(spec, seed))))
        variants[instance.hidden.quiz_variant] += 1
        key_positions[instance.hidden.answer_key] += 1
        again = await generate_instance(spec, seed, deps(FakeLLM(honest(spec, seed))))
        assert again.public.options == instance.public.options
        assert again.hidden.answer_key == instance.hidden.answer_key
    # Оба варианта кейса выпадают, а верный ответ стоит на разных местах.
    assert set(variants) == {variant.id for variant in spec.variants}
    assert len(key_positions) >= 3, key_positions


async def test_case_without_facts_of_variant_is_rewritten_then_model_error():
    spec = quiz_spec()
    llm = FakeLLM(lambda m, s: essay_reply(m, s, keep_facts=False))
    with pytest.raises(ModelOutputError, match="не прошёл"):
        await generate_instance(spec, 5, deps(llm, legend_attempts=2))
    # Каждая попытка — кейс и проверка фактов; вторая получает причину отказа.
    assert len(llm.calls) == 4
    assert "отклонён" in llm.calls[2]["messages"][-1].content


async def test_case_naming_the_answer_is_rewritten():
    spec = quiz_spec()
    seed = 3
    variant = choose_variant(spec, seed)
    attempts = []

    def respond(messages, schema):
        if "situation" in (schema or {}).get("properties", {}):
            attempts.append(messages)
            facts = essay_reply(messages, schema)
            if len(attempts) == 1:
                # Кейс подсказывает ответ запрещённой фразой.
                data = json.loads(facts)
                data["situation"] += " Тут поможет алгоритм Дейкстры."
                return json.dumps(data, ensure_ascii=False)
            return facts
        return essay_reply(messages, schema)

    instance = await generate_instance(spec, seed, deps(FakeLLM(respond)))
    assert len(attempts) == 2
    assert "дейкстр" in attempts[1][-1].content
    assert "Дейкстры" not in instance.statement
    assert instance.hidden.quiz_variant == variant.id


async def test_case_with_formula_spoiled_by_json_escape_is_rewritten():
    spec = quiz_spec()
    attempts = []

    def respond(messages, schema):
        reply = essay_reply(messages, schema)
        if "situation" in (schema or {}).get("properties", {}):
            attempts.append(messages)
            if len(attempts) == 1:
                # \beta одним обратным слэшем: json.loads читает \b как управляющий символ.
                return reply.replace('"situation": "', '"situation": "Вес $\\beta = 2$. ')
        return reply

    instance = await generate_instance(spec, 3, deps(FakeLLM(respond)))
    assert len(attempts) == 2
    assert "обратные слэши" in attempts[1][-1].content
    assert "eta" not in instance.statement


async def test_case_with_number_beyond_facts_is_rewritten():
    spec = quiz_spec()
    attempts = []

    def respond(messages, schema):
        reply = essay_reply(messages, schema)
        if "situation" in (schema or {}).get("properties", {}):
            attempts.append(messages)
            if len(attempts) == 1:
                # Лишний объём может сменить верный ответ, а проверка фактов его не видит.
                data = json.loads(reply)
                data["situation"] += " За день приходит двадцать миллионов заказов."
                return json.dumps(data, ensure_ascii=False)
        return reply

    instance = await generate_instance(spec, 3, deps(FakeLLM(respond)))
    assert len(attempts) == 2
    assert "двадцать миллионов" in attempts[1][-1].content
    assert "миллионов" not in instance.statement


async def test_case_with_numbers_of_facts_is_accepted_at_once():
    spec = quiz_spec(sections={"Варианты": QUIZ_VARIANTS.replace("{n}", "120")})
    llm = FakeLLM(honest(spec, 3))
    instance = await generate_instance(spec, 3, deps(llm, solver_enabled=False))
    assert "120" in instance.statement
    # Кейс и проверка фактов одной попытки: числа фактов отказа не вызывают.
    assert len(llm.calls) == 2


async def test_case_prompt_has_facts_of_chosen_variant_only_and_no_answers():
    spec = quiz_spec()
    seed = 11
    llm = FakeLLM(honest(spec, seed))
    await generate_instance(spec, seed, deps(llm))
    variant = choose_variant(spec, seed)
    other = next(v for v in spec.variants if v.id != variant.id)
    writer = "\n".join(message.content for message in llm.calls[0]["messages"])
    assert all(fact.text.split("{")[0] in writer for fact in variant.facts)
    assert not any(fact.text in writer for fact in other.facts if fact not in variant.facts)
    # Писатель кейса не видит ни вопроса, ни ответов: ему нечего пересказать.
    assert spec.question not in writer
    for answer in (variant.correct, *variant.wrong):
        assert answer not in writer


# --- Слепой решатель -----------------------------------------------------------------------


async def test_solver_sees_case_question_and_options_but_not_key_or_facts():
    spec = quiz_spec()
    seed = 8
    llm = FakeLLM(honest(spec, seed))
    instance = await generate_instance(spec, seed, deps(llm, solver_enabled=True))

    assert len(llm.calls) == 3
    solver = llm.calls[2]
    assert solver["model"] == SOLVER
    prompt = "\n".join(message.content for message in solver["messages"])
    assert instance.statement in prompt
    for option in instance.public.options:
        assert f"- {option.id}: {option.text}" in prompt
    assert "Факты" not in prompt and "+" + choose_variant(spec, seed).correct not in prompt
    assert instance.hidden_dump()["solver"] == {
        "passed": True,
        "attempts": 1,
        "rejected_drafts": 0,
    }


@pytest.mark.parametrize(
    "mistake",
    ["wrong_choice", "two_correct"],
)
async def test_solver_disagreement_rewrites_case_then_model_error(mistake):
    spec = quiz_spec()
    seed = 21
    variant = choose_variant(spec, seed)

    def respond(messages, schema):
        if mistake == "wrong_choice":
            reply = quiz_solver_reply(messages, schema, pick=variant.wrong[0])
        else:
            reply = quiz_solver_reply(
                messages, schema, pick=variant.correct, also_correct=(variant.wrong[1],)
            )
        return reply or essay_reply(messages, schema)

    llm = FakeLLM(respond)
    with pytest.raises(ModelOutputError, match="решател"):
        await generate_instance(spec, seed, deps(llm, solver_enabled=True, legend_attempts=2))
    # Две попытки по три вызова: кейс, факты, решатель; вторая знает причину отказа.
    assert len(llm.calls) == 6
    assert "решател" in llm.calls[3]["messages"][-1].content


async def test_solver_success_after_rewrite_is_recorded():
    spec = quiz_spec()
    seed = 21
    variant = choose_variant(spec, seed)
    solved = []

    def respond(messages, schema):
        if "choice" in (schema or {}).get("properties", {}):
            solved.append(True)
            pick = variant.wrong[0] if len(solved) == 1 else variant.correct
            return quiz_solver_reply(messages, schema, pick=pick)
        return essay_reply(messages, schema)

    instance = await generate_instance(spec, seed, deps(FakeLLM(respond), solver_enabled=True))
    assert instance.hidden_dump()["solver"] == {
        "passed": True,
        "attempts": 2,
        "rejected_drafts": 1,
    }


async def test_solver_attempts_give_more_tries_before_verdict():
    spec = quiz_spec()
    seed = 21
    variant = choose_variant(spec, seed)
    picks = iter([variant.wrong[0], variant.correct])

    def respond(messages, schema):
        if "choice" in (schema or {}).get("properties", {}):
            return quiz_solver_reply(messages, schema, pick=next(picks))
        return essay_reply(messages, schema)

    options = {"solver_enabled": True, "solver_attempts": 2}
    instance = await generate_instance(spec, seed, deps(FakeLLM(respond), **options))
    assert instance.hidden_dump()["solver"] == {
        "passed": True,
        "attempts": 2,
        "rejected_drafts": 0,
    }


async def test_solver_error_means_no_instance():
    spec = quiz_spec()

    def respond(messages, schema):
        if "choice" in (schema or {}).get("properties", {}):
            raise http_error(503)
        return essay_reply(messages, schema)

    with pytest.raises(LLMUnavailableError):
        await generate_instance(spec, 1, deps(FakeLLM(respond), solver_enabled=True))


async def test_solver_out_of_schema_is_not_a_pass():
    spec = quiz_spec()

    def respond(messages, schema):
        # Второй вызов идёт без схемы, поэтому решателя узнаём по системному промпту.
        if messages[0].content.startswith("Ты кандидат"):
            return "не JSON"
        return essay_reply(messages, schema)

    with pytest.raises(ModelOutputError, match="решател"):
        await generate_instance(
            spec, 1, deps(FakeLLM(respond), solver_enabled=True, legend_attempts=1)
        )
