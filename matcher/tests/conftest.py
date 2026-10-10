import hashlib
import json
import random
import re
from collections.abc import Callable
from typing import Any

from matcher.config import (
    ExplanationSettings,
    LLMSettings,
    LLMStageSettings,
    Settings,
)
from matcher.llm.contracts import ChatMessage
from matcher.llm.llm_config import ChatRole, EvidenceSettings, ModelRole, ProviderSettings

# Модели ролей в настройках тестов: FakeLLM узнаёт роль вызова по модели.
REWRITER, RANKER, CHECKER = "fake/rewriter", "fake/ranker", "fake/checker"

Responder = Callable[[list[ChatMessage], dict[str, Any] | None], str]


def llm_settings(**fields: Any) -> Settings:
    """Настройки с включённой LLM-стадией и ролями на подделке FakeLLM."""
    llm = LLMSettings(
        providers={"fake": ProviderSettings(base_url="http://fake")},
        rewriter=ChatRole(model=REWRITER),
        ranker=ChatRole(model=RANKER),
        checker=ChatRole(model=CHECKER),
        embedding=ModelRole(model="fake/embedder"),
    )
    base: dict[str, Any] = {
        "llm": llm,
        "llm_stage": LLMStageSettings(enabled=True, batch_size=8),
        "explanation": ExplanationSettings(rewrites=1),
        "evidence": EvidenceSettings(mode="spans", repairs=0),
    }
    return Settings(_env_file=None, **{**base, **fields})


class FakeLLM:
    """LLMClient без сети: ответ даёт функция роли, которую выбирает модель вызова.

    Функция получает сообщения и схему ответа и возвращает текст ответа; исключение из неё
    уходит вызывающему, как ошибка провайдера. Каждый вызов запоминается.
    """

    def __init__(self, roles: dict[str, Responder]) -> None:
        self.roles = roles
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        json_schema: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        self.calls.append({"model": model, "messages": messages, "json_schema": json_schema})
        if model not in self.roles:
            raise AssertionError(f"вызов модели {model}, а у подделки есть {sorted(self.roles)}")
        return self.roles[model](messages, json_schema)

    def calls_of(self, model: str) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["model"] == model]

    async def aclose(self) -> None:
        """Приложение закрывает своего клиента LLM при остановке."""


_SPAN = re.compile(r"^\[(\d+)\] (.*)$", re.MULTILINE)
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")


def honest_checker(messages: list[ChatMessage], schema: dict[str, Any] | None) -> str:
    """Проверяльщик, который подтверждает каждое предложение строками фактов с его числами.

    Предложение без чисел он подтверждает первой строкой фактов.
    """
    user = messages[-1].content
    spans = {int(n): line for n, line in _SPAN.findall(user)}
    sentences = re.findall(r"^Предложение (\d+): (.*)$", user, re.MULTILINE)
    verdicts = []
    for number, sentence in sentences:
        proof = []
        for raw in _NUMBER.findall(sentence):
            value = float(raw.replace(",", "."))
            proof += [
                n
                for n, line in spans.items()
                if any(float(x) == value for x in _NUMBER.findall(line.rpartition(" = ")[2]))
            ][:1]
        verdicts.append(
            {"sentence": int(number), "supported": True, "reason": "", "spans": proof or [1]}
        )
    return json.dumps({"verdicts": verdicts}, ensure_ascii=False)


def refusing_checker(messages: list[ChatMessage], schema: dict[str, Any] | None) -> str:
    """Проверяльщик, который не подтверждает ни одного предложения."""
    sentences = re.findall(r"^Предложение (\d+): ", messages[-1].content, re.MULTILINE)
    verdicts = [
        {"sentence": int(n), "supported": False, "reason": "не следует из фактов", "spans": []}
        for n in sentences
    ]
    return json.dumps({"verdicts": verdicts}, ensure_ascii=False)


class FakeEmbedder:
    """Эмбеддер без сети: вектор — функция текста, одинаковый текст даёт одинаковый вектор.

    Близость таких векторов не несёт смысла; тестам нужна только воспроизводимость, учёт
    вызовов и нужная размерность.
    """

    def __init__(self, dim: int = 1024, *, model: str = "fake-embedder") -> None:
        self.dim = dim
        self.model_name = model
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self.vector(text) for text in texts]

    def vector(self, text: str) -> list[float]:
        rng = random.Random(hashlib.sha256(text.encode()).hexdigest())
        return [rng.uniform(-1.0, 1.0) for _ in range(self.dim)]
