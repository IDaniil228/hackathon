"""Провайдер на роль: настройки ролей, маршрут вызова к провайдеру роли и ключи провайдеров."""

import asyncio
import json
import os

import httpx
import pytest
from pydantic import ValidationError

from taskgen.config import ChatRole, LLMSettings, Settings
from taskgen.contracts import ChatMessage
from taskgen.llm import MissingAPIKeyError, OpenAICompatibleClient, ProviderRouter
from taskgen.llm_config import LLMSettingsBase, ModelRole, ProviderSettings

MESSAGES = [ChatMessage(role="user", content="Привет")]
SECRET = "sk-test-secret-value"
GIGACHAT = "hf.co/ai-sage/GigaChat3.1-10B-A1.8B-GGUF:q4_K_M"


def _settings(**env: str) -> Settings:
    return Settings(_env_file=None, **env)


def test_defaults_put_generation_local_and_evaluation_in_cloud(monkeypatch):
    for name in list(os.environ):
        if name.upper().startswith("TASKGEN_LLM"):
            monkeypatch.delenv(name)
    llm = _settings().llm
    local = {llm.generator.provider, llm.solver.provider, llm.embedding.provider}
    cloud = {llm.judge.provider, llm.checker.provider, llm.detector.provider}
    assert len(local) == 1 and len(cloud) == 1 and local != cloud
    assert llm.providers[local.pop()].base_url.startswith("http://localhost:11434")
    assert llm.providers[cloud.pop()].base_url == "https://api.deepseek.com"
    assert llm.generator.model == llm.solver.model == f"ollama/{GIGACHAT}"
    assert llm.judge.model == llm.checker.model == llm.detector.model == "deepseek/deepseek-flash"
    assert llm.embedding.model == "ollama/bge-m3"
    assert {role.reasoning_effort for role in llm.chat_roles().values()} == {"none"}


def test_provider_is_the_part_before_the_first_slash():
    # В именах моделей Ollama бывают свои косые черты.
    role = ChatRole(model=f"ollama/{GIGACHAT}")
    assert (role.provider, role.provider_model) == ("ollama", GIGACHAT)


@pytest.mark.parametrize("model", ["deepseek-flash", "/m", "ollama/", ""])
def test_model_without_provider_prefix_is_rejected(model):
    with pytest.raises(ValidationError, match="провайдер/модель"):
        ChatRole(model=model)


def test_key_from_env_keeps_the_rest_of_the_default_provider(monkeypatch):
    monkeypatch.setenv("TASKGEN_LLM__PROVIDERS__DEEPSEEK__API_KEY", SECRET)
    llm = _settings().llm
    assert llm.providers["deepseek"].api_key.get_secret_value() == SECRET
    assert llm.providers["deepseek"].base_url == "https://api.deepseek.com"
    assert "ollama" in llm.providers
    assert SECRET not in repr(llm) and SECRET not in llm.model_dump_json()


def test_env_moves_a_role_to_another_provider_and_keeps_its_effort(monkeypatch):
    monkeypatch.setenv("TASKGEN_LLM__JUDGE__MODEL", "ollama/qwen3.5:4b")
    llm = _settings().llm
    assert (llm.judge.provider, llm.judge.provider_model) == ("ollama", "qwen3.5:4b")
    assert llm.judge.reasoning_effort == "none"
    assert llm.checker.provider == "deepseek"


def test_env_adds_a_new_provider_for_one_role(monkeypatch):
    monkeypatch.setenv("TASKGEN_LLM__PROVIDERS__VLLM__BASE_URL", "http://gpu:8000/v1")
    monkeypatch.setenv("TASKGEN_LLM__GENERATOR__MODEL", f"vllm/{GIGACHAT}")
    llm = _settings().llm
    # Та же модель у двух провайдеров законна: решатель остаётся в Ollama.
    assert llm.generator.provider == "vllm" and llm.solver.provider == "ollama"
    assert llm.generator.provider_model == llm.solver.provider_model == GIGACHAT
    assert llm.providers["vllm"].base_url == "http://gpu:8000/v1"
    assert {"ollama", "deepseek", "vllm"} <= llm.providers.keys()


def test_role_on_unknown_provider_is_rejected_with_the_known_ones():
    with pytest.raises(ValidationError, match="nowhere.*deepseek, ollama"):
        LLMSettings(judge=ChatRole(model="nowhere/m"))


class SlowLLM:
    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.models: list[str | None] = []

    async def complete(
        self, messages, *, model=None, temperature=0.7, json_schema=None, reasoning_effort=None
    ) -> str:
        self.models.append(model)
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return "ok"


def _two_providers(local_limit: int = 1, cloud_limit: int = 1) -> LLMSettings:
    return LLMSettings(
        providers={
            "ollama": ProviderSettings(base_url="http://local", concurrency=local_limit),
            "deepseek": ProviderSettings(base_url="http://cloud", concurrency=cloud_limit),
        }
    )


async def test_router_sends_the_call_to_the_prefix_provider_without_the_prefix():
    settings = _two_providers()
    local, cloud = SlowLLM(), SlowLLM()
    router = ProviderRouter(settings, {"ollama": local, "deepseek": cloud})
    await router.complete(MESSAGES, model=settings.generator.model)
    await router.complete(MESSAGES, model="deepseek/any-model")
    await router.complete(MESSAGES, model=f"deepseek/{GIGACHAT}")
    await router.complete(MESSAGES)
    assert local.models == [GIGACHAT, GIGACHAT]
    assert cloud.models == ["any-model", GIGACHAT]


async def test_router_limits_each_provider_separately():
    settings = _two_providers(local_limit=1, cloud_limit=3)
    local, cloud = SlowLLM(), SlowLLM()
    router = ProviderRouter(settings, {"ollama": local, "deepseek": cloud})
    calls = [router.complete(MESSAGES, model=settings.generator.model) for _ in range(4)]
    calls += [router.complete(MESSAGES, model=settings.judge.model) for _ in range(6)]
    await asyncio.gather(*calls)
    assert (local.peak, cloud.peak) == (1, 3)


@pytest.mark.parametrize("model", ["unknown-model", "nowhere/m"])
async def test_router_rejects_a_model_without_a_known_provider(model):
    router = ProviderRouter(_two_providers(), {"ollama": SlowLLM(), "deepseek": SlowLLM()})
    with pytest.raises(ValueError, match=model):
        await router.complete(MESSAGES, model=model)


def test_router_reports_the_structured_output_of_the_prefix_provider():
    settings = LLMSettings()
    router = ProviderRouter(settings, {"ollama": SlowLLM(), "deepseek": SlowLLM()})
    assert router.structured_output(settings.generator.model) == "json_schema"
    assert router.structured_output(settings.judge.model) == "json_object"
    assert router.structured_output(None) == "json_schema"


@pytest.mark.parametrize(
    ("mode", "response_format"),
    [("json_schema", "json_schema"), ("json_object", "json_object"), ("text", None)],
)
async def test_client_maps_the_schema_to_the_provider_structured_output(mode, response_format):
    client, requests = _recording_client(
        ProviderSettings(base_url="http://cloud", structured_output=mode)
    )
    await client.complete(MESSAGES, model="m", json_schema={"type": "object"})
    await client.complete(MESSAGES, model="m")
    first, plain = (json.loads(request.content) for request in requests)
    assert first.get("response_format", {}).get("type") == response_format
    assert "response_format" not in plain


def _recording_client(provider: ProviderSettings, status: int = 200):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "bad key"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ок"}}]})

    transport = httpx.MockTransport(handler)
    variable = LLMSettings().key_variable("deepseek")
    client = OpenAICompatibleClient(
        provider, name="deepseek", key_variable=variable, transport=transport
    )
    return client, requests


async def test_client_sends_the_key_only_when_it_is_set():
    with_key, requests = _recording_client(
        ProviderSettings(base_url="http://cloud", api_key=SECRET)
    )
    await with_key.complete(MESSAGES, model="m")
    without_key, bare = _recording_client(ProviderSettings(base_url="http://local"))
    await without_key.complete(MESSAGES, model="m")
    assert requests[0].headers["authorization"] == f"Bearer {SECRET}"
    assert "authorization" not in bare[0].headers
    assert json.loads(requests[0].content)["model"] == "m"


async def test_missing_required_key_fails_before_the_request_and_names_the_variable():
    client, requests = _recording_client(
        ProviderSettings(base_url="http://cloud", requires_key=True)
    )
    with pytest.raises(MissingAPIKeyError, match="TASKGEN_LLM__PROVIDERS__DEEPSEEK__API_KEY"):
        await client.complete(MESSAGES, model="m")
    assert not requests
    # Генерация и оценка считают любую httpx.HTTPError недоступностью модели.
    assert issubclass(MissingAPIKeyError, httpx.HTTPError)


async def test_rejected_key_is_logged_with_the_variable(caplog):
    client, _ = _recording_client(
        ProviderSettings(base_url="http://cloud", api_key="wrong"), status=401
    )
    with caplog.at_level("ERROR", logger="taskgen.llm"), pytest.raises(httpx.HTTPStatusError):
        await client.complete(MESSAGES, model="m")
    (record,) = [r for r in caplog.records if r.name == "taskgen.llm"]
    assert "TASKGEN_LLM__PROVIDERS__DEEPSEEK__API_KEY" in record.getMessage()
    assert "wrong" not in record.getMessage()


async def test_router_from_settings_warns_about_a_missing_key_and_still_starts(caplog):
    settings = LLMSettings()
    with caplog.at_level("WARNING", logger="taskgen.llm"):
        router = ProviderRouter.from_settings(settings)
    try:
        messages = " ".join(r.getMessage() for r in caplog.records if r.name == "taskgen.llm")
        assert "TASKGEN_LLM__PROVIDERS__DEEPSEEK__API_KEY" in messages
    finally:
        await router.aclose()


class _OtherService(LLMSettingsBase):
    """Настройки LLM другого сервиса: свой префикс, роли без генератора."""

    env_prefix = "OTHER_"

    ranker: ChatRole = ChatRole(model="deepseek/deepseek-flash")
    embedding: ModelRole = ModelRole(model="ollama/bge-m3")


async def test_key_variable_follows_the_prefix_of_the_service(caplog):
    settings = _OtherService()
    with caplog.at_level("WARNING", logger="taskgen.llm"):
        router = ProviderRouter.from_settings(settings)
    try:
        messages = " ".join(r.getMessage() for r in caplog.records if r.name == "taskgen.llm")
        assert "OTHER_LLM__PROVIDERS__DEEPSEEK__API_KEY" in messages
        assert "TASKGEN_" not in messages
        with pytest.raises(MissingAPIKeyError, match="OTHER_LLM__PROVIDERS__DEEPSEEK__API_KEY"):
            await router.complete(MESSAGES, model=settings.ranker.model)
    finally:
        await router.aclose()


async def test_call_without_a_model_fails_when_the_service_has_no_default_model():
    router = ProviderRouter(_OtherService(), {"ollama": SlowLLM(), "deepseek": SlowLLM()})
    with pytest.raises(ValueError, match="без модели"):
        await router.complete(MESSAGES)
    assert await router.complete(MESSAGES, model="ollama/m") == "ok"


async def test_router_from_settings_builds_clients_only_for_used_providers():
    settings = LLMSettings(
        providers={"spare": ProviderSettings(base_url="http://spare")},
        judge=ChatRole(model="ollama/qwen3.5:4b"),
        checker=ChatRole(model="ollama/qwen3.5:4b"),
        detector=ChatRole(model="ollama/qwen3.5:4b"),
    )
    router = ProviderRouter.from_settings(settings)
    try:
        assert router.providers() == {"ollama"}
    finally:
        await router.aclose()


async def test_router_embeds_through_the_embedding_provider():
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]})

    settings = LLMSettings()
    client = OpenAICompatibleClient(
        settings.providers["ollama"],
        name="ollama",
        key_variable=settings.key_variable("ollama"),
        transport=httpx.MockTransport(handler),
    )
    router = ProviderRouter(settings, {"ollama": client, "deepseek": SlowLLM()})
    assert router.model_name == "bge-m3"
    assert await router.embed(["текст"]) == [[1.0, 0.0]]
    assert bodies == [{"model": "bge-m3", "input": ["текст"]}]


class _ChatOnlyService(LLMSettingsBase):
    env_prefix = "CHAT_"
    parser: ChatRole = ChatRole(model="ollama/m")


async def test_chat_service_does_not_need_embedding_configuration():
    router = ProviderRouter(_ChatOnlyService(), {"ollama": SlowLLM()})
    assert await router.complete(MESSAGES, model="ollama/m") == "ok"
    with pytest.raises(ValueError, match="embedding"):
        await router.embed(["текст"])
