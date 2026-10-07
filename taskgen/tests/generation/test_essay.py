"""Ситуация эссе: промпт, сборка условия и проверка перед выдачей.

Главная фикстура — настоящий вывод qwen3.5:4b по старому шаблону ml-validation-leakage-essay:
условие подсказало ответ, потеряло подбор гиперпараметров на отчётной выборке и исказило
вопрос. Проверка обязана отклонить его. Проверяльщик здесь — подделка, которая отвечает так,
как честный читатель отвечает по содержанию фикстуры.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from taskgen.bank.parser import parse_template
from taskgen.config import EvidenceSettings
from taskgen.contracts import ChatMessage, TemplateSpec
from taskgen.evaluation.evidence import split_spans
from taskgen.generation.essay import (
    EssayScenario,
    EssayScenarioError,
    build_statement,
    check_essay_scenario,
    essay_questions,
    generate_essay,
    scenario_messages,
)
from tests.evaluation.fakes import QUOTES, FakeLLM

SEED = Path(__file__).resolve().parents[2] / "seed" / "templates"
LEAKAGE = SEED / "ml" / "junior" / "ml-validation-leakage-essay.md"
BAD = Path(__file__).parent / "samples" / "ml-validation-leakage-essay-qwen3.5-4b.json"
MARKER = re.compile(r"<<<СИТУАЦИЯ-([0-9a-f]+)>>>")

# Что честный читатель находит в испорченной генерации; цитаты скопированы из неё.
# Случайное разбиение там есть только как указание кандидату, а не как шаг аналитика.
HONEST_BAD_VERDICTS: dict[str, list[str]] = {
    "task": ["прогнозирования оттока клиентов мобильного оператора на основе месячной истории"],
    "boosting": ["модель градиентного бустинга"],
    "train-score": ["получив точность 0.99 только на обучающей выборке"],
    "worse-after-launch": ["После запуска модели на новых данных оценка качества заметно упала"],
    "impute-all": ["заполнение пропусков средним для всего набора"],
    "scale-all": ["масштабирование признаков по всем данным одновременно"],
}

GOOD_SITUATION = (
    "Маша — начинающий аналитик в мобильном операторе, она строит модель для задачи: "
    "прогноз оттока клиентов мобильного оператора по месячной истории. "
    "Данные копились восемь месяцев, у каждой строки есть дата выгрузки. "
    "Сначала Маша заполнила пропуски средним значением, которое посчитала по всей таблице "
    "сразу, до разбиения. Затем отмасштабировала признаки, взяв среднее и разброс тоже по "
    "всей таблице до разбиения. После этого она случайно разбила строки на две части. "
    "Маша обучила градиентный бустинг и перебирала глубину и число деревьев, глядя на "
    "качество второй части, а потом по той же второй части написала в отчёте итоговое качество. "
    "На обучающей части точность модели была 0.99. Через месяц после запуска на новых "
    "клиентах качество оказалось заметно хуже."
)
GOOD_QUOTES: dict[str, list[str]] = {
    "task": ["прогноз оттока клиентов мобильного оператора по месячной истории"],
    "boosting": ["Маша обучила градиентный бустинг"],
    "train-score": ["На обучающей части точность модели была 0.99"],
    "worse-after-launch": ["на новых клиентах качество оказалось заметно хуже"],
    "impute-all": ["заполнила пропуски средним значением, которое посчитала по всей таблице"],
    "scale-all": ["взяв среднее и разброс тоже по всей таблице до разбиения"],
    "random-split": ["она случайно разбила строки на две части"],
    "tuning-on-report": [
        "перебирала глубину и число деревьев, глядя на качество второй части",
        "по той же второй части написала в отчёте итоговое качество",
    ],
    "dated-records": ["Данные копились восемь месяцев, у каждой строки есть дата выгрузки"],
}
PARAMS = {"domain": "прогноз оттока клиентов мобильного оператора по месячной истории"}


def leakage_spec() -> TemplateSpec:
    return parse_template(LEAKAGE.read_text(encoding="utf-8"), source="ml-validation-leakage")


def bad_scenario() -> EssayScenario:
    sample = json.loads(BAD.read_text(encoding="utf-8"))
    return EssayScenario(title=sample["public"]["title"], situation=sample["statement"])


def checker_reply(verdicts: dict[str, list[str]], spec: TemplateSpec) -> str:
    items = [
        {"fact_id": fact.id, "present": fact.id in verdicts, "quotes": verdicts.get(fact.id, [])}
        for fact in spec.scenario_facts
    ]
    return json.dumps({"facts": items}, ensure_ascii=False)


def checker(verdicts: dict[str, list[str]], spec: TemplateSpec) -> FakeLLM:
    return FakeLLM(lambda _messages, _schema: checker_reply(verdicts, spec))


async def check(llm: FakeLLM, spec: TemplateSpec, scenario: EssayScenario):
    return await check_essay_scenario(
        llm, spec, scenario, PARAMS, model="checker", evidence_options=QUOTES
    )


# --- Проверка на настоящей испорченной генерации -------------------------------------------


async def test_real_bad_generation_is_rejected():
    spec = leakage_spec()

    result = await check(checker(HONEST_BAD_VERDICTS, spec), spec, bad_scenario())

    assert not result.ok
    # Пункт 2 сам назвал источники утечки, а заголовок и пункт 1 — переобучение.
    assert {"утечк", "переобуч"} <= set(result.forbidden_found)
    # Подбор гиперпараметров на отчётной выборке исчез, а рубрика tuning-leak его проверяет.
    assert "tuning-on-report" in result.missing_facts
    assert result.reasons


async def test_real_bad_generation_is_rejected_by_facts_alone():
    spec = leakage_spec().model_copy(update={"forbidden": []})

    result = await check(checker(HONEST_BAD_VERDICTS, spec), spec, bad_scenario())

    assert not result.ok and result.forbidden_found == []
    # Ровно то, чего честный читатель не нашёл: цитаты на остальные факты настоящие.
    assert set(result.missing_facts) == {"tuning-on-report", "random-split", "dated-records"}


async def test_good_situation_passes():
    spec = leakage_spec()

    result = await check(
        checker(GOOD_QUOTES, spec), spec, EssayScenario(title="Отток", situation=GOOD_SITUATION)
    )

    assert result.ok, result.reasons
    assert result.missing_facts == [] and result.forbidden_found == [] and result.reasons == []


# --- Цитаты проверяет код --------------------------------------------------------------------


async def test_invented_quote_does_not_count():
    spec = leakage_spec()
    # Проверяльщик выдумал подтверждение: такой фразы в генерации нет.
    verdicts = HONEST_BAD_VERDICTS | {
        "tuning-on-report": ["гиперпараметры подбирал по той же части, на которой отчитался"]
    }

    result = await check(checker(verdicts, spec), spec, bad_scenario())

    assert "tuning-on-report" in result.missing_facts


async def test_present_without_quote_does_not_count():
    spec = leakage_spec()
    verdicts = GOOD_QUOTES | {"tuning-on-report": []}

    result = await check(
        checker(verdicts, spec), spec, EssayScenario(title="Отток", situation=GOOD_SITUATION)
    )

    assert result.missing_facts == ["tuning-on-report"] and not result.ok


async def test_fact_missing_from_checker_reply_is_missing():
    spec = leakage_spec()
    verdicts = {k: v for k, v in GOOD_QUOTES.items() if k != "dated-records"}

    result = await check(
        checker(verdicts, spec), spec, EssayScenario(title="Отток", situation=GOOD_SITUATION)
    )

    assert result.missing_facts == ["dated-records"]


async def test_checker_reply_out_of_schema_fails_closed():
    spec = leakage_spec()
    llm = FakeLLM(lambda _messages, _schema: "не знаю")

    result = await check(llm, spec, EssayScenario(title="Отток", situation=GOOD_SITUATION))

    assert not result.ok
    assert result.missing_facts == [fact.id for fact in spec.scenario_facts]


# --- Запрещённые фразы -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("phrase", "text"),
    [
        ("подсчет", "Сделали ПОДСЧЁТ заново"),
        ("подсчёт", "сделали подсчет заново"),
        ("select_related", "добавили select-related в view"),
        ("ранней остановк", "Помогла  ранней,\nостановки бы"),
    ],
)
async def test_forbidden_phrase_is_found_through_normalization(phrase: str, text: str):
    spec = leakage_spec().model_copy(update={"forbidden": [phrase]})
    situation = f"{GOOD_SITUATION} {text}."

    result = await check(
        checker(GOOD_QUOTES, spec), spec, EssayScenario(title="Отток", situation=situation)
    )

    assert result.forbidden_found == [phrase] and not result.ok


async def test_forbidden_phrase_in_title_is_found():
    spec = leakage_spec()
    scenario = EssayScenario(title="Утечка в модели оттока", situation=GOOD_SITUATION)

    result = await check(checker(GOOD_QUOTES, spec), spec, scenario)

    assert result.forbidden_found == ["утечк"]


async def test_unclosed_code_block_is_rejected():
    # Вопросы идут после ситуации: незакрытый блок кода спрятал бы их внутри кода.
    spec = leakage_spec()
    situation = f"{GOOD_SITUATION}\n\n```python\nmodel.fit(X, y)\n"

    result = await check(
        checker(GOOD_QUOTES, spec), spec, EssayScenario(title="Отток", situation=situation)
    )

    assert not result.ok and result.reasons


# --- Ситуация передаётся проверяльщику как данные --------------------------------------------


async def test_situation_is_delimited_by_random_marker():
    spec = leakage_spec()
    llm = checker(GOOD_QUOTES, spec)
    scenario = EssayScenario(title="Отток", situation=GOOD_SITUATION)

    await check(llm, spec, scenario)
    await check(llm, spec, scenario)

    nonces = []
    for call in llm.calls:
        system, user = (m.content for m in call["messages"])
        nonce = MARKER.search(system).group(1)
        start, end = f"<<<СИТУАЦИЯ-{nonce}>>>", f"<<<КОНЕЦ-{nonce}>>>"
        assert user.index(start) < user.index(GOOD_SITUATION) < user.index(end)
        nonces.append(nonce)
    assert nonces[0] != nonces[1]


async def test_checker_sees_facts_with_params_filled():
    spec = leakage_spec()
    llm = checker(GOOD_QUOTES, spec)

    await check(llm, spec, EssayScenario(title="Отток", situation=GOOD_SITUATION))

    user = llm.calls[0]["messages"][1].content
    assert f"аналитик строит модель для задачи: {PARAMS['domain']}" in user
    assert "{domain}" not in user


# --- Промпт ситуации и сборка условия --------------------------------------------------------


def test_scenario_prompt_carries_generation_and_facts_but_not_questions():
    spec = leakage_spec()

    messages = scenario_messages(spec, PARAMS)

    user = messages[-1].content
    assert PARAMS["domain"] in user and "{domain}" not in user
    for fact in spec.scenario_facts:
        assert fact.text.replace("{domain}", PARAMS["domain"]) in user
    # Модель не видит вопросов: ей нечего пересказать и не на что ответить.
    assert spec.questions not in user and "1. Почему качество" not in user


def test_statement_is_situation_then_questions_verbatim():
    spec = leakage_spec()
    situation = "Ситуация про отток."

    statement = build_statement(situation, essay_questions(spec, PARAMS))

    assert statement.startswith(situation)
    assert statement.endswith(spec.questions)


def test_questions_get_params_filled():
    spec = parse_template(
        (SEED / "algo" / "senior" / "algo-streaming-top-k-essay.md").read_text(encoding="utf-8"),
        source="top-k",
    ).model_copy(update={"questions": "Памяти {memory_mb} МБ, скобки {{x}}."})

    assert essay_questions(spec, {"memory_mb": 64}) == "Памяти 64 МБ, скобки {x}."


# --- Цикл "написал — проверил — переписал" ---------------------------------------------------


def writer_and_checker(situations: list[str], spec: TemplateSpec) -> FakeLLM:
    """Писатель отдаёт ситуации по очереди, проверяльщик честно цитирует ту, что получил."""
    pending = iter(situations)

    def respond(messages: list[ChatMessage], schema: dict[str, Any] | None) -> str:
        if schema is not None and "facts" in schema["properties"]:
            text = messages[1].content
            verdicts = {
                fid: quotes
                for fid, quotes in GOOD_QUOTES.items()
                if all(quote in text for quote in quotes)
            }
            return checker_reply(verdicts, spec)
        return json.dumps({"title": "Отток", "situation": next(pending)}, ensure_ascii=False)

    return FakeLLM(respond)


async def test_failed_check_is_retried_with_reasons():
    spec = leakage_spec()
    without_tuning = GOOD_SITUATION.replace(
        "перебирала глубину и число деревьев, глядя на качество второй части", "обучила"
    )
    llm = writer_and_checker([without_tuning, GOOD_SITUATION], spec)

    draft = await generate_essay(
        llm,
        spec,
        PARAMS,
        model="writer",
        checker_model="checker",
        temperature=0.7,
        attempts=3,
        evidence_options=QUOTES,
    )

    assert draft.situation == GOOD_SITUATION
    assert draft.statement == build_statement(GOOD_SITUATION, essay_questions(spec, PARAMS))
    rewrite = [c for c in llm.calls if c["model"] == "writer"][1]["messages"][-1].content
    tuning = next(f for f in spec.scenario_facts if f.id == "tuning-on-report")
    assert tuning.text in rewrite


async def test_attempts_exhausted_raise_with_last_check():
    spec = leakage_spec()
    leaking = GOOD_SITUATION + " Это классическая утечка."
    llm = writer_and_checker([leaking, leaking], spec)

    with pytest.raises(EssayScenarioError) as caught:
        await generate_essay(
            llm,
            spec,
            PARAMS,
            model="writer",
            checker_model="checker",
            temperature=0.7,
            attempts=2,
            evidence_options=QUOTES,
        )

    assert caught.value.check.forbidden_found == ["утечк"]
    assert len([c for c in llm.calls if c["model"] == "writer"]) == 2


# --- Ссылки на участки ситуации --------------------------------------------------------------

GOOD_SENTENCES = split_spans(GOOD_SITUATION, code=False)


def numbers_of(quotes: list[str]) -> list[int]:
    """Номера предложений GOOD_SITUATION, в которых стоят цитаты."""
    return [i for i, sentence in enumerate(GOOD_SENTENCES, 1) if any(q in sentence for q in quotes)]


GOOD_SPANS = {fid: numbers_of(quotes) for fid, quotes in GOOD_QUOTES.items()}


def spans_checker(verdicts: dict[str, list[int]], spec: TemplateSpec, repaired=None) -> FakeLLM:
    def reply(chosen: dict[str, list[int]]) -> str:
        items = [
            {"fact_id": fact.id, "present": fact.id in chosen, "spans": chosen.get(fact.id, [])}
            for fact in spec.scenario_facts
        ]
        return json.dumps({"facts": items}, ensure_ascii=False)

    def respond(messages: list[ChatMessage], _schema) -> str:
        if repaired is not None and "не принят" in messages[-1].content:
            return reply(repaired)
        return reply(verdicts)

    return FakeLLM(respond)


async def check_spans(llm: FakeLLM, spec: TemplateSpec, repairs: int = 0):
    return await check_essay_scenario(
        llm,
        spec,
        EssayScenario(title="Отток", situation=GOOD_SITUATION),
        PARAMS,
        model="checker",
        evidence_options=EvidenceSettings(mode="spans", repairs=repairs),
    )


async def test_spans_checker_sees_numbered_sentences_and_their_text_is_evidence():
    spec = leakage_spec()
    llm = spans_checker(GOOD_SPANS, spec)

    result = await check_spans(llm, spec)

    assert result.ok, result.reasons
    split = "После этого она случайно разбила строки на две части."
    assert result.evidence["random-split"] == split
    user = llm.calls[0]["messages"][1].content
    assert f"[5] {split}" in user.splitlines()
    assert "spans" in json.dumps(llm.calls[0]["json_schema"])


async def test_spans_checker_nonexistent_span_does_not_confirm_fact():
    spec = leakage_spec()
    llm = spans_checker(GOOD_SPANS | {"dated-records": [len(GOOD_SENTENCES) + 1]}, spec)

    result = await check_spans(llm, spec)

    assert result.missing_facts == ["dated-records"] and not result.ok
    assert len(llm.calls) == 1


async def test_checker_reply_without_some_facts_is_asked_again():
    spec = leakage_spec()
    partial = {k: v for k, v in GOOD_SPANS.items() if k != "dated-records"}

    def respond(messages: list[ChatMessage], _schema) -> str:
        repaired = "не принят" in messages[-1].content
        chosen = GOOD_SPANS if repaired else partial
        facts = (
            spec.scenario_facts
            if repaired
            else [f for f in spec.scenario_facts if f.id != "dated-records"]
        )
        items = [{"fact_id": f.id, "present": True, "spans": chosen[f.id]} for f in facts]
        return json.dumps({"facts": items}, ensure_ascii=False)

    llm = FakeLLM(respond)
    result = await check_spans(llm, spec, repairs=1)

    assert result.ok, result.reasons
    assert "dated-records" in llm.calls[1]["messages"][-1].content


async def test_spans_checker_is_asked_again_about_nonexistent_span():
    spec = leakage_spec()
    bad = GOOD_SPANS | {"dated-records": [99]}
    llm = spans_checker(bad, spec, repaired=GOOD_SPANS)

    result = await check_spans(llm, spec, repairs=1)

    assert result.ok, result.reasons
    assert len(llm.calls) == 2
    assert "99" in llm.calls[1]["messages"][-1].content
