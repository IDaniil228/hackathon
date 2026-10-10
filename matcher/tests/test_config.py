"""Настройки LLM матчера: переменные окружения с префиксом MATCHER_ и ключи провайдеров."""

import os

import pytest

from matcher.config import Settings
from matcher.llm.contracts import ChatMessage
from matcher.llm.llm import MissingAPIKeyError, ProviderRouter

SECRET = "sk-test-secret-value"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.upper().startswith(("MATCHER_LLM", "TASKGEN_LLM")):
            monkeypatch.delenv(name)


def test_key_from_env_keeps_the_rest_of_the_default_provider(monkeypatch) -> None:
    monkeypatch.setenv("MATCHER_LLM__PROVIDERS__DEEPSEEK__API_KEY", SECRET)
    llm = Settings(_env_file=None).llm
    assert llm.providers["deepseek"].api_key.get_secret_value() == SECRET
    assert llm.providers["deepseek"].base_url == "https://api.deepseek.com"


def test_role_from_env_keeps_its_reasoning_effort(monkeypatch) -> None:
    monkeypatch.setenv("MATCHER_LLM__RANKER__MODEL", "ollama/qwen3.5:4b")
    llm = Settings(_env_file=None).llm
    assert llm.ranker.model == "ollama/qwen3.5:4b" and llm.ranker.reasoning_effort == "none"


async def test_missing_key_names_the_matcher_variable(caplog) -> None:
    llm = Settings(_env_file=None).llm
    with caplog.at_level("WARNING"):
        router = ProviderRouter.from_settings(llm)
    try:
        assert "MATCHER_LLM__PROVIDERS__DEEPSEEK__API_KEY" in caplog.text
        assert "TASKGEN_" not in caplog.text
        with pytest.raises(MissingAPIKeyError, match="MATCHER_LLM__PROVIDERS__DEEPSEEK"):
            await router.complete([ChatMessage(role="user", content="x")], model=llm.ranker.model)
    finally:
        await router.aclose()


async def test_call_without_model_is_an_error() -> None:
    llm = Settings(_env_file=None).llm
    router = ProviderRouter(llm, {"ollama": object(), "deepseek": object()})
    with pytest.raises(ValueError, match="без модели"):
        await router.complete([ChatMessage(role="user", content="x")])
