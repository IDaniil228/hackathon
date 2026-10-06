"""Проверка ситуации эссе эмбеддингами и режим "both" с LLM для серой зоны.

Эмбеддер здесь — таблица "текст → вектор": косинусное сходство в каждом тесте задано
явно, и порог проверяется на известных числах, а не на поведении настоящей модели.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from taskgen.contracts import ChatMessage, ScenarioFact, TemplateSpec
from taskgen.evaluation.quotes import EmbeddingCache
from taskgen.generation.essay import (
    EssayCheckOptions,
    EssayScenario,
    check_essay_scenario,
    generate_essay,
    split_sentences,
)
from tests.evaluation.fakes import FakeLLM, TableEmbedder, unit
from tests.generation.test_essay import leakage_spec

FACT_A = "аналитик обучил градиентный бустинг"
FACT_B = "данные копились несколько месяцев"
PHRASE = "утечк"

S_BOOST = "Маша обучила бустинг на деревьях."
S_DATES = "Таблица собиралась полгода."
S_HINT = "Сведения из проверки просочились в обучение."
S_PLAIN = "Отчёт ушёл руководителю."


def small_spec(*, forbidden: list[str] | None = None) -> TemplateSpec:
    return leakage_spec().model_copy(
        update={
            "scenario_facts": [
                ScenarioFact(id="boosting", text=FACT_A),
                ScenarioFact(id="dates", text=FACT_B),
            ],
            "forbidden": [PHRASE] if forbidden is None else forbidden,
        }
    )


def vectors(**sentences: list[float]) -> dict[str, list[float]]:
    """Факт A — ось 0, факт B — ось 1, фраза — ось 2; заголовок ни на что не похож."""
    table = {
        FACT_A: unit(1.0, 0),
        FACT_B: unit(1.0, 1),
        PHRASE: unit(1.0, 2),
        "Отток": unit(0.0, 3),
    }
    table.update({text.replace("_", " "): vector for text, vector in sentences.items()})
    return table


def options(method: str = "embeddings", **overrides: Any) -> EssayCheckOptions:
    values = {"fact_threshold": 0.7, "forbidden_threshold": 0.8, "grey_zone": 0.05}
    values.update(overrides)
    return EssayCheckOptions(method=method, **values)


def no_llm() -> FakeLLM:
    def refuse(_messages: list[ChatMessage], _schema: dict[str, Any] | None) -> str:
        raise AssertionError("в этом режиме проверка не должна звать LLM")

    return FakeLLM(refuse)


async def run(
    llm: FakeLLM,
    spec: TemplateSpec,
    situation: str,
    embedder: TableEmbedder | None,
    opts: EssayCheckOptions,
    cache: EmbeddingCache | None = None,
):
    return await check_essay_scenario(
        llm,
        spec,
        EssayScenario(title="Отток", situation=situation),
        {},
        model="checker",
        options=opts,
        embedder=embedder,
        cache=cache if cache is not None else EmbeddingCache(),
    )


# --- Разбиение на предложения ----------------------------------------------------------------


def test_sentences_split_on_terminal_punctuation_and_lines():
    text = "Первое предложение. Второе! Третье?\nЧетвёртое без точки\n\nПятое…  Шестое."

    assert split_sentences(text) == [
        "Первое предложение.",
        "Второе!",
        "Третье?",
        "Четвёртое без точки",
        "Пятое…",
        "Шестое.",
    ]


def test_decimal_point_and_list_number_do_not_split():
    text = "1. Точность на обучении 0.99, а потом хуже.\n2. Вариант второй."

    assert split_sentences(text) == [
        "1. Точность на обучении 0.99, а потом хуже.",
        "2. Вариант второй.",
    ]


def test_code_block_is_one_unit():
    code = "```python\nclass Order(models.Model):\n    user = models.ForeignKey(User)\n```"
    text = f"Вот модели. Они простые.\n\n{code}\n\nПосле кода текст."

    assert split_sentences(text) == ["Вот модели.", "Они простые.", code, "После кода текст."]


def test_blank_text_has_no_sentences():
    assert split_sentences("  \n\n ") == []


# --- Режим embeddings ------------------------------------------------------------------------


async def test_fact_is_present_at_threshold_and_missing_below():
    situation = f"{S_BOOST} {S_DATES}"
    embedder = TableEmbedder(vectors(**{S_BOOST: unit(0.70, 0), S_DATES: unit(0.69, 1)}))

    result = await run(no_llm(), small_spec(forbidden=[]), situation, embedder, options())

    assert result.missing_facts == ["dates"]
    assert result.evidence["boosting"] == S_BOOST
    assert result.method == "embeddings" and not result.ok


async def test_evidence_is_the_most_similar_sentence():
    situation = f"{S_PLAIN} {S_BOOST} {S_DATES}"
    embedder = TableEmbedder(
        vectors(**{S_PLAIN: unit(0.75, 0), S_BOOST: unit(0.9, 0), S_DATES: unit(0.9, 1)})
    )

    result = await run(no_llm(), small_spec(forbidden=[]), situation, embedder, options())

    assert result.ok, result.reasons
    assert result.evidence == {"boosting": S_BOOST, "dates": S_DATES}


async def test_forbidden_phrase_has_its_own_threshold():
    situation = f"{S_BOOST} {S_DATES} {S_HINT}"
    table = vectors(**{S_BOOST: unit(0.9, 0), S_DATES: unit(0.9, 1), S_HINT: unit(0.75, 2)})

    strict = await run(no_llm(), small_spec(), situation, TableEmbedder(table), options())
    loose = await run(
        no_llm(),
        small_spec(),
        situation,
        TableEmbedder(table),
        options(forbidden_threshold=0.75),
    )

    # 0.75 выше порога фактов 0.7, но ниже порога запрещённых 0.8.
    assert strict.forbidden_found == [] and strict.ok, strict.reasons
    assert loose.forbidden_found == [PHRASE] and not loose.ok
    assert loose.forbidden_evidence[PHRASE] == S_HINT


async def test_forbidden_below_threshold_is_not_found():
    situation = f"{S_BOOST} {S_DATES} {S_HINT}"
    table = vectors(**{S_BOOST: unit(0.9, 0), S_DATES: unit(0.9, 1), S_HINT: unit(0.79, 2)})

    result = await run(no_llm(), small_spec(), situation, TableEmbedder(table), options())

    assert result.forbidden_found == [] and result.ok, result.reasons


async def test_substring_search_stays_in_embeddings_mode():
    # Эмбеддинги не видят сходства, но фраза стоит в тексте дословно.
    literal = "Коллега назвал это утечкой."
    situation = f"{S_BOOST} {S_DATES} {literal}"
    table = vectors(**{S_BOOST: unit(0.9, 0), S_DATES: unit(0.9, 1), literal: unit(0.0, 3)})

    result = await run(no_llm(), small_spec(), situation, TableEmbedder(table), options())

    assert result.forbidden_found == [PHRASE] and not result.ok


async def test_template_vectors_are_cached_between_instances():
    cache = EmbeddingCache()
    spec = small_spec()
    first = f"{S_BOOST} {S_DATES}"
    second = f"{S_DATES} {S_BOOST} {S_PLAIN}"
    table = vectors(**{S_BOOST: unit(0.9, 0), S_DATES: unit(0.9, 1), S_PLAIN: unit(0.1, 0)})
    embedder = TableEmbedder(table)

    await run(no_llm(), spec, first, embedder, options(), cache)
    await run(no_llm(), spec, second, embedder, options(), cache)

    embedded = embedder.embedded()
    for template_text in (FACT_A, FACT_B, PHRASE):
        assert embedded.count(template_text) == 1, embedded
    # Предложения ситуации меняются от экземпляра к экземпляру и в кэш не идут.
    assert embedded.count(S_BOOST) == 2


async def test_cache_is_per_embedding_model():
    cache = EmbeddingCache()
    situation = f"{S_BOOST} {S_DATES}"
    table = vectors(**{S_BOOST: unit(0.9, 0), S_DATES: unit(0.9, 1)})
    first, other = TableEmbedder(table, model="m1"), TableEmbedder(table, model="m2")

    await run(no_llm(), small_spec(), situation, first, options(), cache)
    await run(no_llm(), small_spec(), situation, other, options(), cache)

    assert FACT_A in other.embedded()


# --- Деградация без эмбеддера ----------------------------------------------------------------


def honest_checker(present: dict[str, list[str]], phrases: dict[str, list[str]] | None = None):
    """Проверяльщик-подделка: present — факты с цитатами, phrases — найденные фразы."""

    def respond(messages: list[ChatMessage], schema: dict[str, Any] | None) -> str:
        properties = (schema or {})["properties"]
        reply: dict[str, Any] = {}
        if "facts" in properties:
            ids = properties["facts"]["items"]["properties"]["fact_id"]["enum"]
            reply["facts"] = [
                {"fact_id": fid, "present": fid in present, "quotes": present.get(fid, [])}
                for fid in ids
            ]
        if "phrases" in properties:
            listed = properties["phrases"]["items"]["properties"]["phrase"]["enum"]
            found = phrases or {}
            reply["phrases"] = [
                {"phrase": p, "present": p in found, "quotes": found.get(p, [])} for p in listed
            ]
        return json.dumps(reply, ensure_ascii=False)

    return FakeLLM(respond)


async def test_embeddings_without_embedder_fall_back_to_llm_with_note():
    situation = f"{S_BOOST} {S_DATES}"
    llm = honest_checker({"boosting": [S_BOOST], "dates": [S_DATES]})

    result = await run(llm, small_spec(forbidden=[]), situation, None, options())

    assert result.ok, result.reasons
    assert result.method == "llm" and len(llm.calls) == 1
    assert any("эмбеддер" in note for note in result.notes)


async def test_embedder_error_falls_back_to_llm_with_note():
    situation = f"{S_BOOST} {S_DATES}"
    embedder = TableEmbedder({})
    embedder.fail = RuntimeError("эмбеддер упал")
    llm = honest_checker({"boosting": [S_BOOST]})

    result = await run(llm, small_spec(forbidden=[]), situation, embedder, options("both"))

    assert result.method == "llm" and result.missing_facts == ["dates"]
    assert any("эмбеддер упал" in note for note in result.notes)


# --- Режим both: LLM только для серой зоны ---------------------------------------------------


def listed_in_call(call: dict[str, Any]) -> tuple[list[str], list[str]]:
    properties = call["json_schema"]["properties"]
    facts = properties.get("facts", {}).get("items", {}).get("properties", {})
    phrases = properties.get("phrases", {}).get("items", {}).get("properties", {})
    return facts.get("fact_id", {}).get("enum", []), phrases.get("phrase", {}).get("enum", [])


async def test_both_asks_llm_only_about_grey_facts_and_phrases():
    situation = f"{S_BOOST} {S_DATES} {S_HINT}"
    # Факт A уверенно есть, факт B в серой зоне 0.7±0.05, фраза в серой зоне 0.8±0.05.
    table = vectors(**{S_BOOST: unit(0.95, 0), S_DATES: unit(0.72, 1), S_HINT: unit(0.78, 2)})
    llm = honest_checker({}, {PHRASE: [S_HINT]})

    result = await run(llm, small_spec(), situation, TableEmbedder(table), options("both"))

    assert len(llm.calls) == 1
    assert listed_in_call(llm.calls[0]) == (["dates"], [PHRASE])
    # Серый факт решила LLM (не нашла), уверенный факт — эмбеддинги.
    assert result.missing_facts == ["dates"]
    assert result.forbidden_found == [PHRASE]
    assert result.method == "both"


async def test_both_skips_llm_when_nothing_is_grey():
    situation = f"{S_BOOST} {S_DATES} {S_HINT}"
    table = vectors(**{S_BOOST: unit(0.95, 0), S_DATES: unit(0.30, 1), S_HINT: unit(0.20, 2)})

    result = await run(no_llm(), small_spec(), situation, TableEmbedder(table), options("both"))

    assert result.missing_facts == ["dates"] and result.forbidden_found == []


async def test_both_grey_fact_confirmed_by_llm_is_present():
    situation = f"{S_BOOST} {S_DATES}"
    table = vectors(**{S_BOOST: unit(0.95, 0), S_DATES: unit(0.66, 1)})
    llm = honest_checker({"dates": [S_DATES]})

    result = await run(
        llm, small_spec(forbidden=[]), situation, TableEmbedder(table), options("both")
    )

    assert result.ok, result.reasons
    assert listed_in_call(llm.calls[0]) == (["dates"], [])


async def test_both_grey_fact_with_invented_quote_is_missing():
    situation = f"{S_BOOST} {S_DATES}"
    table = vectors(**{S_BOOST: unit(0.95, 0), S_DATES: unit(0.72, 1)})
    llm = honest_checker({"dates": ["данные копились восемь месяцев"]})

    result = await run(
        llm, small_spec(forbidden=[]), situation, TableEmbedder(table), options("both")
    )

    assert result.missing_facts == ["dates"]


# --- LLM-проверяльщик и запрещённые понятия --------------------------------------------------


async def test_llm_mode_asks_about_phrases_not_found_by_substring():
    spec = small_spec(forbidden=[PHRASE, "переобуч"])
    situation = f"{S_BOOST} {S_DATES} {S_HINT} Это утечка."
    llm = honest_checker({"boosting": [S_BOOST], "dates": [S_DATES]})

    result = await run(llm, spec, situation, None, options("llm"))

    # "утечк" найдена подстрокой, LLM спрашивают только про оставшуюся фразу.
    assert listed_in_call(llm.calls[0])[1] == ["переобуч"]
    assert result.forbidden_found == [PHRASE]


async def test_llm_phrase_verdict_needs_a_real_quote():
    spec = small_spec()
    situation = f"{S_BOOST} {S_DATES} {S_HINT}"
    present = {"boosting": [S_BOOST], "dates": [S_DATES]}

    quoted = await run(
        honest_checker(present, {PHRASE: [S_HINT]}), spec, situation, None, options("llm")
    )
    invented = await run(
        honest_checker(present, {PHRASE: ["данные утекли"]}), spec, situation, None, options("llm")
    )

    assert quoted.forbidden_found == [PHRASE] and not quoted.ok
    assert invented.forbidden_found == [] and invented.ok, invented.reasons


# --- Проверка смысла цитат проверяльщика -----------------------------------------------------

# Предложение ситуации, которое есть в тексте, но к факту B отношения не имеет.
RELEVANCE_TABLE = {S_BOOST: unit(0.9, 0), S_DATES: unit(0.9, 1), S_PLAIN: unit(0.1, 1)}


async def test_checker_quote_off_topic_for_fact_does_not_confirm_it():
    situation = f"{S_BOOST} {S_PLAIN}"
    llm = honest_checker({"boosting": [S_BOOST], "dates": [S_PLAIN]})
    embedder = TableEmbedder(vectors(**RELEVANCE_TABLE))

    result = await run(
        llm, small_spec(forbidden=[]), situation, embedder, options("llm", quote_threshold=0.5)
    )

    assert result.missing_facts == ["dates"] and not result.ok
    assert result.evidence == {"boosting": S_BOOST}


async def test_checker_quote_on_topic_confirms_fact():
    situation = f"{S_BOOST} {S_DATES}"
    llm = honest_checker({"boosting": [S_BOOST], "dates": [S_DATES]})
    embedder = TableEmbedder(vectors(**RELEVANCE_TABLE))

    result = await run(
        llm, small_spec(forbidden=[]), situation, embedder, options("llm", quote_threshold=0.5)
    )

    assert result.ok, result.reasons
    assert result.notes == []


async def test_checker_relevance_is_skipped_with_note_without_embedder():
    situation = f"{S_BOOST} {S_PLAIN}"
    llm = honest_checker({"boosting": [S_BOOST], "dates": [S_PLAIN]})

    result = await run(
        llm, small_spec(forbidden=[]), situation, None, options("llm", quote_threshold=0.5)
    )

    assert result.ok, result.reasons
    assert any("смысла цитат" in note for note in result.notes)


async def test_checker_relevance_does_not_clear_forbidden_phrase():
    # Найденная фраза отклоняет ситуацию; проверка смысла её не снимает, иначе она
    # ослабила бы проверку, а не усилила.
    situation = f"{S_BOOST} {S_DATES} {S_HINT}"
    llm = honest_checker({"boosting": [S_BOOST], "dates": [S_DATES]}, {PHRASE: [S_HINT]})
    table = vectors(**RELEVANCE_TABLE, **{S_HINT: unit(0.0, 2)})

    result = await run(
        llm, small_spec(), situation, TableEmbedder(table), options("llm", quote_threshold=0.5)
    )

    assert result.forbidden_found == [PHRASE] and not result.ok


# --- Генерация во всех режимах ---------------------------------------------------------------


@pytest.mark.parametrize("method", ["llm", "embeddings", "both"])
async def test_generate_essay_works_in_every_mode(method: str):
    spec = small_spec(forbidden=[])
    situation = f"{S_BOOST} {S_DATES}"
    table = vectors(**{S_BOOST: unit(0.95, 0), S_DATES: unit(0.95, 1)})

    def respond(messages: list[ChatMessage], schema: dict[str, Any] | None) -> str:
        if "situation" in schema["properties"]:
            return json.dumps({"title": "Отток", "situation": situation}, ensure_ascii=False)
        return honest_checker({"boosting": [S_BOOST], "dates": [S_DATES]})._responder(
            messages, schema
        )

    llm = FakeLLM(respond)

    draft = await generate_essay(
        llm,
        spec,
        {},
        model="writer",
        checker_model="checker",
        temperature=0.7,
        attempts=1,
        check_options=options(method),
        embedder=TableEmbedder(table),
        embedding_cache=EmbeddingCache(),
    )

    assert draft.check.ok and draft.check.method == method
    checker_calls = [c for c in llm.calls if c["model"] == "checker"]
    assert len(checker_calls) == (1 if method == "llm" else 0)
