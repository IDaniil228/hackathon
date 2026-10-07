"""Проверка фактов ситуации эссе моделью NLI на модели-подделке."""

from __future__ import annotations

import json
import sys
from typing import Any

import pytest

from taskgen.config import GenerationSettings
from taskgen.contracts import ChatMessage, ScenarioFact
from taskgen.generation import nli as nli_module
from taskgen.generation.essay import (
    EssayCheckOptions,
    EssayScenario,
    check_essay_scenario,
    generate_essay,
)
from taskgen.generation.nli import (
    FactInference,
    Inference,
    NLIUnavailableError,
    OnnxNLI,
    fact_inferences,
    fact_present,
    model_from_settings,
    premise_units,
)
from tests.evaluation.fakes import FakeLLM
from tests.generation.test_essay_embeddings import (
    FACT_A,
    FACT_B,
    PHRASE,
    S_BOOST,
    S_DATES,
    S_HINT,
    honest_checker,
    listed_in_call,
    no_llm,
    small_spec,
)


class FakeNLI:
    """Исход задан парой (посылка, гипотеза); остальные пары нейтральны."""

    name = "fake-nli"

    def __init__(self, table: dict[tuple[str, str], Inference]) -> None:
        self.table = table
        self.pairs: list[tuple[str, str]] = []

    async def infer(self, pairs: list[tuple[str, str]]) -> list[Inference]:
        self.pairs.extend(pairs)
        return [self.table.get(pair, Inference(0.0, 0.0)) for pair in pairs]


FACT = ScenarioFact(id="index", text="На поле email есть индекс.")


def test_window_two_adds_adjacent_pairs_only():
    assert premise_units(["А.", "Б.", "В."], 1) == ["А.", "Б.", "В."]
    assert premise_units(["А.", "Б.", "В."], 2) == ["А.", "Б.", "В.", "А. Б.", "Б. В."]


def test_window_zero_is_the_whole_situation():
    assert premise_units(["А.", "Б.", "В."], 0) == ["А. Б. В."]
    assert premise_units([], 0) == []


def test_single_sentence_has_no_window():
    assert premise_units(["А."], 2) == ["А."]


async def test_fact_takes_best_entailment_and_worst_contradiction():
    model = FakeNLI(
        {
            ("Индекс на email добавили.", FACT.text): Inference(0.9, 0.01),
            ("Индекса на email нет.", FACT.text): Inference(0.05, 0.8),
        }
    )
    sentences = ["Индекс на email добавили.", "Индекса на email нет.", "Про другое."]
    [(fid, score)] = (await fact_inferences(model, [FACT], sentences)).items()
    assert fid == "index"
    assert score == FactInference(0.9, 0.8, "Индекс на email добавили.")


async def test_fact_split_over_two_sentences_is_found_only_with_window():
    joined = "Таблица users большая. Поле email в ней индексировано."
    model = FakeNLI({(joined, FACT.text): Inference(0.95, 0.0)})
    sentences = ["Таблица users большая.", "Поле email в ней индексировано."]
    alone = await fact_inferences(model, [FACT], sentences, window=1)
    paired = await fact_inferences(model, [FACT], sentences, window=2)
    assert alone["index"].entailment == 0.0
    assert paired["index"] == FactInference(0.95, 0.0, joined)


async def test_premise_comes_first_and_fact_second():
    model = FakeNLI({})
    await fact_inferences(model, [FACT], ["Ситуация."])
    assert model.pairs == [("Ситуация.", FACT.text)]


async def test_situation_without_sentences_has_no_fact():
    score = (await fact_inferences(FakeNLI({}), [FACT], []))["index"]
    assert not fact_present(score, 0.5, None)


def test_threshold_decides_without_veto():
    assert fact_present(FactInference(0.5, 0.99, "x"), 0.5, None)
    assert not fact_present(FactInference(0.49, 0.0, "x"), 0.5, None)


def test_contradiction_vetoes_entailed_fact():
    assert not fact_present(FactInference(0.9, 0.7, "x"), 0.5, 0.7)
    assert fact_present(FactInference(0.9, 0.69, "x"), 0.5, 0.7)


@pytest.mark.parametrize("package", ["numpy", "onnxruntime", "tokenizers", "huggingface_hub"])
async def test_missing_dependency_names_the_install_command(monkeypatch, package):
    # None в sys.modules заставляет import поднять ImportError, даже если пакет стоит.
    monkeypatch.setitem(sys.modules, package, None)
    model = OnnxNLI("some/repo")
    with pytest.raises(NLIUnavailableError, match="uv sync --group nli"):
        await model.infer([("а", "б")])


def test_only_nli_mode_builds_a_model(monkeypatch):
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    assert model_from_settings(GenerationSettings(essay_check_method="both")) is None
    with pytest.raises(NLIUnavailableError, match="uv sync --group nli"):
        model_from_settings(GenerationSettings(essay_check_method="nli"))


def test_nli_model_comes_from_settings(monkeypatch):
    monkeypatch.setattr(nli_module, "import_runtime", lambda: None)
    options = GenerationSettings(
        essay_check_method="nli", essay_nli_model="org/model", essay_nli_revision="abc"
    )

    model = model_from_settings(options)

    assert isinstance(model, OnnxNLI)
    assert (model.repo, model.onnx_file, model.revision) == ("org/model", "onnx/model.onnx", "abc")


# --- Режим nli проверки ситуации -------------------------------------------------------------


def nli_options(**overrides: Any) -> EssayCheckOptions:
    values: dict[str, Any] = {
        "fact_threshold": 0.7,
        "forbidden_threshold": 0.8,
        "grey_zone": 0.05,
        "nli_threshold": 0.5,
        "nli_contradiction": 0.9,
    }
    values.update(overrides)
    return EssayCheckOptions(method="nli", **values)


def entails(*sentences: tuple[str, str]) -> FakeNLI:
    """Каждая пара (предложение, факт) — уверенный entailment."""
    return FakeNLI({pair: Inference(0.95, 0.01) for pair in sentences})


async def run_nli(llm: FakeLLM, situation: str, nli: FakeNLI | None, opts=None, spec=None):
    return await check_essay_scenario(
        llm,
        spec or small_spec(),
        EssayScenario(title="Отток", situation=situation),
        {},
        model="checker",
        options=opts or nli_options(),
        nli=nli,
    )


async def test_nli_decides_facts_and_llm_only_phrases():
    situation = f"{S_BOOST} {S_DATES} {S_HINT}"
    llm = honest_checker({}, {PHRASE: [S_HINT]})

    result = await run_nli(llm, situation, entails((S_BOOST, FACT_A), (S_DATES, FACT_B)))

    assert result.method == "nli" and result.missing_facts == []
    assert len(llm.calls) == 1 and listed_in_call(llm.calls[0]) == ([], [PHRASE])
    assert result.forbidden_found == [PHRASE] and not result.ok
    assert result.evidence == {"boosting": S_BOOST, "dates": S_DATES}


async def test_nli_negated_fact_is_missing_and_llm_is_not_asked():
    negation = "Таблица собиралась всего один день."
    nli = entails((S_BOOST, FACT_A), (negation, FACT_B))
    nli.table[(negation, FACT_B)] = Inference(0.6, 0.95)

    result = await run_nli(no_llm(), f"{S_BOOST} {negation}", nli)

    assert result.missing_facts == ["dates"] and not result.ok


async def test_nli_fact_below_threshold_is_missing():
    nli = entails((S_BOOST, FACT_A))
    nli.table[(S_DATES, FACT_B)] = Inference(0.49, 0.0)

    result = await run_nli(no_llm(), f"{S_BOOST} {S_DATES}", nli)

    assert result.missing_facts == ["dates"]


async def test_nli_window_finds_fact_split_over_two_sentences():
    first, second = "Таблицу начали собирать в марте.", "Закончили её только в августе."
    nli = entails((S_BOOST, FACT_A), (f"{first} {second}", FACT_B))
    llm = honest_checker({})
    situation = f"{S_BOOST} {first} {second}"

    alone = await run_nli(no_llm(), situation, nli)
    paired = await run_nli(llm, situation, nli, nli_options(nli_window=2))

    assert alone.missing_facts == ["dates"]
    assert paired.missing_facts == [] and paired.ok


async def test_nli_literal_phrase_rejects_without_llm():
    situation = f"{S_BOOST} {S_DATES} Это утечка."
    nli = entails((S_BOOST, FACT_A), (S_DATES, FACT_B))

    result = await run_nli(no_llm(), situation, nli)

    assert result.forbidden_found == [PHRASE] and not result.ok


async def test_nli_without_llm_phrases_never_calls_llm():
    nli = entails((S_BOOST, FACT_A), (S_DATES, FACT_B))

    result = await run_nli(
        no_llm(), f"{S_BOOST} {S_DATES} {S_HINT}", nli, nli_options(nli_llm_phrases=False)
    )

    assert result.ok and result.method == "nli"


async def test_nli_without_model_falls_back_to_llm_with_note():
    llm = honest_checker({"boosting": [S_BOOST], "dates": [S_DATES]})

    result = await run_nli(llm, f"{S_BOOST} {S_DATES}", None, spec=small_spec(forbidden=[]))

    assert result.ok and result.method == "llm" and len(llm.calls) == 1
    assert any("NLI" in note for note in result.notes)


async def test_nli_error_falls_back_to_llm_with_note():
    class Broken(FakeNLI):
        async def infer(self, pairs):
            raise NLIUnavailableError("модель не скачалась")

    llm = honest_checker({"boosting": [S_BOOST]})

    result = await run_nli(llm, f"{S_BOOST} {S_DATES}", Broken({}), spec=small_spec(forbidden=[]))

    assert result.method == "llm" and result.missing_facts == ["dates"]
    assert any("модель не скачалась" in note for note in result.notes)


async def test_generate_essay_passes_nli_to_the_check():
    situation = f"{S_BOOST} {S_DATES}"
    nli = entails((S_BOOST, FACT_A), (S_DATES, FACT_B))

    def respond(messages: list[ChatMessage], schema: dict[str, Any] | None) -> str:
        assert schema is not None and "situation" in schema["properties"]
        return json.dumps({"title": "Отток", "situation": situation}, ensure_ascii=False)

    draft = await generate_essay(
        FakeLLM(respond),
        small_spec(forbidden=[]),
        {},
        model="writer",
        checker_model="checker",
        temperature=0.0,
        attempts=1,
        check_options=nli_options(),
        nli=nli,
    )

    assert draft.check.method == "nli" and draft.check.ok


async def test_empty_request_does_not_load_the_model(monkeypatch):
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    assert await OnnxNLI("some/repo").infer([]) == []
