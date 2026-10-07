"""Настройки генерации живут в общих настройках сервиса и переопределяются окружением."""

from __future__ import annotations

from types import SimpleNamespace

from taskgen.config import EvidenceSettings, Settings
from taskgen.evaluation.service import Evaluator
from taskgen.generation.service import Generator
from tests.evaluation.fakes import FakeLLM
from tests.generation.fakes import BagOfWordsEmbedder


def test_generation_settings_come_from_service_settings(monkeypatch):
    monkeypatch.setenv("TASKGEN_GENERATION__LEGEND_ATTEMPTS", "7")
    settings = Settings()
    assert settings.generation.legend_attempts == 7
    state = SimpleNamespace(settings=settings, sessionmaker=None, llm=FakeLLM([]))
    assert Generator.from_state(state).options is settings.generation


def test_essay_check_settings_reach_instance_deps(monkeypatch):
    monkeypatch.setenv("TASKGEN_GENERATION__ESSAY_CHECK_METHOD", "both")
    monkeypatch.setenv("TASKGEN_GENERATION__ESSAY_GREY_ZONE", "0.07")
    embedder = BagOfWordsEmbedder()
    state = SimpleNamespace(
        settings=Settings(), sessionmaker=None, llm=FakeLLM([]), embedder=embedder
    )

    first = Generator.from_state(state).instance_deps()
    second = Generator.from_state(state).instance_deps()

    assert first.options.essay_check_method == "both"
    assert first.options.essay_grey_zone == 0.07
    assert first.embedder is embedder
    # Генератор собирается на каждый запрос, а кэш векторов шаблонов живёт в app.state.
    assert first.embedding_cache is not None
    assert first.embedding_cache is second.embedding_cache


def test_one_evidence_block_reaches_judge_and_checker(monkeypatch):
    monkeypatch.setenv("TASKGEN_EVIDENCE__MODE", "quotes")
    monkeypatch.setenv("TASKGEN_EVIDENCE__REPAIRS", "5")
    monkeypatch.setenv("TASKGEN_EVIDENCE__MAX_SPANS", "3")
    state = SimpleNamespace(settings=Settings(), sessionmaker=None, llm=FakeLLM([]))

    deps = Generator.from_state(state).instance_deps()
    judge = Evaluator.from_state(state).judge

    expected = EvidenceSettings(mode="quotes", repairs=5, max_spans=3)
    assert deps.evidence == expected
    assert judge._evidence == expected
