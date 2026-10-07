"""Проверка фактов ситуации эссе моделью NLI на модели-подделке."""

from __future__ import annotations

import sys

import pytest

from taskgen.contracts import ScenarioFact
from taskgen.generation.nli import (
    FactInference,
    Inference,
    NLIUnavailableError,
    OnnxNLI,
    fact_inferences,
    fact_present,
    premise_units,
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


async def test_empty_request_does_not_load_the_model(monkeypatch):
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    assert await OnnxNLI("some/repo").infer([]) == []
