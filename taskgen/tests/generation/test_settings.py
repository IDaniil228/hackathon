"""Настройки генерации живут в общих настройках сервиса и переопределяются окружением."""

from __future__ import annotations

from types import SimpleNamespace

from taskgen.config import Settings
from taskgen.generation.service import Generator
from tests.evaluation.fakes import FakeLLM


def test_generation_settings_come_from_service_settings(monkeypatch):
    monkeypatch.setenv("TASKGEN_GENERATION__LEGEND_ATTEMPTS", "7")
    settings = Settings()
    assert settings.generation.legend_attempts == 7
    state = SimpleNamespace(settings=settings, sessionmaker=None, llm=FakeLLM([]))
    assert Generator.from_state(state).options is settings.generation
