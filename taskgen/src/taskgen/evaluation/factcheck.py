"""Текст модели по фактам: барьер кодом, LLM-проверяльщик с доказательством и шаблон.

Модель пишет текст предложениями по фактам в JSON. Текст идёт дальше, только если прошёл
две проверки. Барьер кодом отклоняет число, которого нет в фактах, и запрещённый ярлык.
LLM-проверяльщик сверяет каждое предложение с фактами и доказывает вердикт строками фактов
(fact_lines); среди строк доказательства должно быть каждое число предложения. Отклонённый
текст модель переписывает с причинами отказа, а после последней попытки остаётся текст по
шаблону. Смысл фактов, промпт проверяльщика и шаблон задаёт вызывающий: machine summary в
taskgen и объяснение выдачи в matcher.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel

from taskgen.contracts import ChatMessage, LLMClient, ReasoningEffort
from taskgen.evaluation.evidence import Grounding
from taskgen.evaluation.jsonout import StructuredOutputError, complete_structured
from taskgen.llm_config import EvidenceSettings
from taskgen.markers import has_marker, markers, new_nonce

logger = logging.getLogger(__name__)

NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")

# Ярлыки, которые приписывают кандидату нечестность или авторство ИИ; шаблон для re.compile с
# re.IGNORECASE. Вызывающий дополняет его своими ярлыками через |.
HONESTY_AND_AI_LABELS = (
    r"списыв|списал|\bИИ\b|\bAI\b|нейросет|искусственн\w* интеллект|chatgpt|\bgpt|copilot"
    r"|читер|жульн|обман|плагиат|нечестн|мошен|подозрит|сгенерирова"
)

# Ответ писателя: каждое предложение — отдельный элемент.
SENTENCES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sentences"],
    "properties": {"sentences": {"type": "array", "items": {"type": "string"}}},
}


class Sentences(BaseModel):
    sentences: list[str]


def numbers_in(data: Any) -> list[float]:
    """Числа фактов в тех видах, которыми их может назвать текст."""
    if isinstance(data, bool) or data is None:
        return []
    if isinstance(data, int | float):
        value = float(data)
        found = [value, round(value, 1), round(value, 2)]
        # Доли из фактов текст может назвать процентами: 0.333 → 33%.
        if isinstance(data, float) and 0.0 <= value <= 1.0:
            found += [round(value * 100), round(value * 100, 1)]
        return found
    if isinstance(data, dict):
        return [number for value in data.values() for number in numbers_in(value)]
    if isinstance(data, list):
        return [number for value in data for number in numbers_in(value)]
    return []


def unknown_numbers(text: str, known: list[float]) -> list[str]:
    """Числа текста, которых нет среди known, в том виде, как они записаны в тексте."""
    return [
        raw
        for raw in NUMBER.findall(text)
        if not any(abs(float(raw.replace(",", ".")) - value) < 1e-9 for value in known)
    ]


def barrier_problems(
    text: str,
    data: Any,
    *,
    labels: re.Pattern[str],
    forbid_markers: bool = False,
    max_chars: int | None = None,
) -> list[str]:
    """Нарушения, которые код находит без модели; пустой список — текст идёт дальше.

    Это барьер до проверяльщика: число, которого нет в data, ярлык из labels, а по выбору
    вызывающего — маркер сервиса (markers.has_marker) и длина больше max_chars. Верность
    утверждений, в которых числа взяты из фактов, этот барьер не проверяет.
    """
    problems: list[str] = []
    if label := labels.search(text):
        problems.append(f"оценочный ярлык: {label.group(0)!r}")
    if forbid_markers and has_marker(text):
        problems.append("в тексте служебный маркер")
    if max_chars is not None and len(text) > max_chars:
        problems.append(f"текст длиннее {max_chars} символов")
    problems += [f"числа {raw} нет в фактах" for raw in unknown_numbers(text, numbers_in(data))]
    return problems


def fact_lines(data: Any, path: str = "") -> list[str]:
    """Факты построчно: путь к значению в data и само значение в JSON.

    Строка — участок, которым проверяльщик доказывает предложение (Grounding). Путь называет
    место значения в фактах, а значение стоит в строке целиком. Пустой объект и пустой
    список — тоже значения.
    """
    if isinstance(data, dict) and data:
        return [
            line
            for key, value in data.items()
            for line in fact_lines(value, f"{path}.{key}" if path else key)
        ]
    if isinstance(data, list) and data:
        return [
            line
            for index, value in enumerate(data)
            for line in fact_lines(value, f"{path}[{index}]")
        ]
    return [f"{path} = {json.dumps(data, ensure_ascii=False)}"]


@dataclass(frozen=True)
class CheckerPrompt:
    """Промпт проверяльщика одного вида текста.

    system — шаблон str.format с полями glossary, evidence, facts_start, facts_end,
    text_start и text_end. text_label и text_end_label — метки маркеров текста на проверку.
    """

    system: str
    glossary: str
    text_label: str
    text_end_label: str


class _SentenceVerdict(BaseModel):
    sentence: int
    supported: bool
    reason: str = ""
    quotes: list[str] = []
    spans: list[int] = []


class _CheckerReply(BaseModel):
    verdicts: list[_SentenceVerdict]


def _checker_schema(count: int, grounding: Grounding) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["verdicts"],
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["sentence", "supported", "reason", grounding.field],
                    "properties": {
                        "sentence": {"type": "integer", "minimum": 1, "maximum": count},
                        "supported": {"type": "boolean"},
                        "reason": {"type": "string"},
                        grounding.field: grounding.schema(),
                    },
                },
            }
        },
    }


def _checker_messages(
    sentences: list[str], grounding: Grounding, prompt: CheckerPrompt
) -> list[ChatMessage]:
    nonce = new_nonce()
    facts_start, facts_end = markers("ФАКТЫ", nonce, "КОНЕЦ-ФАКТОВ")
    text_start, text_end = markers(prompt.text_label, nonce, prompt.text_end_label)
    system = prompt.system.format(
        glossary=prompt.glossary,
        evidence=grounding.rules("supported = true", "фактов"),
        facts_start=facts_start,
        facts_end=facts_end,
        text_start=text_start,
        text_end=text_end,
    )
    listing = "\n".join(
        f"Предложение {number}: {sentence}" for number, sentence in enumerate(sentences, 1)
    )
    user = (
        f"{facts_start}\n{grounding.block()}\n{facts_end}\n\n"
        f"{text_start}\n{listing}\n{text_end}\n\n"
        "Для каждого предложения верни вердикт и доказательство в JSON с полем verdicts, "
        f'например {{"verdicts": [{{"sentence": 1, "supported": true, "reason": "", '
        f"{grounding.example()}}}]}}."
    )
    return [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)]


def _evidence_numbers(texts: list[str]) -> list[float]:
    """Числа значений строк доказательства; путь строки, например sessions[0], не в счёт."""
    found: list[float] = []
    for text in texts:
        value = text.rpartition(" = ")[2]
        # Значение в строке записано json.dumps: дробь всегда с точкой, и доля 1.0 даёт 100%.
        for raw in NUMBER.findall(value):
            found += numbers_in(float(raw) if "." in raw else int(raw))
    return found


def _verdict_problem(verdict: _SentenceVerdict, sentence: str, grounding: Grounding) -> str | None:
    """Претензия к вердикту, по которой проверяльщика переспрашивают, или None."""
    if not verdict.supported:
        return None if verdict.reason.strip() else "нет причины отказа"
    texts, problem = grounding.resolve(verdict.quotes, verdict.spans)
    if problem is not None:
        return problem
    if missing := unknown_numbers(sentence, _evidence_numbers(texts)):
        return f"в доказательстве нет строк с числами {', '.join(missing)}"
    return None


def _checker_problems(
    reply: _CheckerReply, sentences: list[str], grounding: Grounding
) -> list[str]:
    problems = []
    given = {verdict.sentence for verdict in reply.verdicts}
    missing = [str(n) for n in range(1, len(sentences) + 1) if n not in given]
    if missing:
        problems.append(f"нет вердикта по предложениям {', '.join(missing)}")
    for verdict in reply.verdicts:
        if not 1 <= verdict.sentence <= len(sentences):
            problems.append(f"предложения {verdict.sentence} нет")
            continue
        problem = _verdict_problem(verdict, sentences[verdict.sentence - 1], grounding)
        if problem is not None:
            problems.append(f"предложение {verdict.sentence}: {problem}")
    return problems


@dataclass(frozen=True)
class FactCheck:
    ok: bool
    # Причины отказа по-русски, по одной на неподтверждённое предложение.
    reasons: list[str] = field(default_factory=list)
    # Строки фактов, которыми проверяльщик подтвердил предложение, по его номеру с 1.
    evidence: dict[int, list[str]] = field(default_factory=dict)


async def check_facts(
    llm: LLMClient,
    sentences: list[str],
    data: Any,
    *,
    prompt: CheckerPrompt,
    model: str | None,
    reasoning_effort: ReasoningEffort | None = None,
    evidence: EvidenceSettings,
) -> FactCheck:
    """Сверка каждого предложения с фактами LLM-проверяльщиком.

    Предложение подтверждено, если проверяльщик так решил и доказал это строками фактов
    (fact_lines), а среди строк есть каждое число предложения. Вердикт с доказательством,
    которое не прошло проверку, после повторов evidence.repairs не засчитывается. Ответ не по
    схеме — отказ. Сетевые ошибки и ошибки провайдера не скрываются.
    """
    grounding = Grounding.of(evidence, "\n".join(fact_lines(data)), code=True)
    try:
        reply = await complete_structured(
            llm,
            _checker_messages(sentences, grounding, prompt),
            _CheckerReply,
            _checker_schema(len(sentences), grounding),
            model=model,
            temperature=0.0,
            reasoning_effort=reasoning_effort,
            check=lambda checked: _checker_problems(checked, sentences, grounding),
            repairs=evidence.repairs,
        )
    except StructuredOutputError:
        return FactCheck(ok=False, reasons=["проверяльщик не ответил по схеме"])
    verdicts: dict[int, list[_SentenceVerdict]] = {}
    for verdict in reply.verdicts:
        verdicts.setdefault(verdict.sentence, []).append(verdict)
    reasons: list[str] = []
    proven: dict[int, list[str]] = {}
    for number, sentence in enumerate(sentences, 1):
        given = verdicts.get(number, [])
        refused = [verdict for verdict in given if not verdict.supported]
        if not given:
            reasons.append(f"нет вердикта: {sentence!r}")
        elif refused:
            reasons.append(f"{refused[0].reason.strip() or 'не подтверждено'}: {sentence!r}")
        elif problem := next(
            filter(None, (_verdict_problem(v, sentence, grounding) for v in given)), None
        ):
            reasons.append(f"доказательство не принято ({problem}): {sentence!r}")
        else:
            proven[number] = [
                text for v in given for text in grounding.resolve(v.quotes, v.spans)[0]
            ]
    return FactCheck(ok=not reasons, reasons=reasons, evidence=proven)


# Писатель: по причинам отказа прошлого варианта (пусто в первый раз) возвращает предложения
# или StructuredOutputError.
Writer = Callable[[list[str]], Awaitable[list[str]]]


def sentences_writer(
    llm: LLMClient,
    *,
    system: str,
    request: str,
    rewrite: str,
    model: str | None,
    reasoning_effort: ReasoningEffort | None = None,
    temperature: float = 0.2,
) -> Writer:
    """Писатель одним вызовом модели по схеме SENTENCES_SCHEMA.

    Повтор получает тот же запрос, причины отказа и просьбу rewrite, например "Перепиши
    справку.".
    """

    async def write(reasons: list[str]) -> list[str]:
        retry = (
            "\n\nПредыдущий вариант отклонён: " + "; ".join(reasons) + ". " + rewrite
            if reasons
            else ""
        )
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=request + retry),
        ]
        reply = await complete_structured(
            llm,
            messages,
            Sentences,
            SENTENCES_SCHEMA,
            model=model,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )
        return reply.sentences

    return write


@dataclass(frozen=True)
class CheckedText:
    text: str
    # True — текст модели прошёл барьер и проверяльщика; False — текст по шаблону.
    verified: bool


async def write_checked(
    llm: LLMClient,
    data: Any,
    *,
    writer: Writer,
    fallback: Callable[[], str],
    prompt: CheckerPrompt,
    labels: re.Pattern[str],
    checker_model: str | None,
    checker_reasoning_effort: ReasoningEffort | None = None,
    evidence: EvidenceSettings,
    attempts: int,
    first: list[str] | None = None,
    forbid_markers: bool = False,
    max_chars: int | None = None,
    subject: str = "Текст",
) -> CheckedText:
    """Текст писателя, если он прошёл барьер barrier_problems и проверяльщика check_facts,
    иначе fallback().

    first — уже готовый первый вариант: тогда первая попытка не зовёт писателя. Отклонённый
    текст писатель переписывает с причинами отказа, всего не больше attempts попыток. Ошибки
    сети и провайдера писателя не скрываются. Проверяльщик, недоступный по сети или без
    ключа, сразу даёт текст по шаблону: непроверенный текст читатель не видит. subject
    называет текст в журнале.
    """
    reasons: list[str] = []
    for attempt in range(attempts):
        try:
            drafted = first if attempt == 0 and first is not None else await writer(reasons)
        except StructuredOutputError as error:
            reasons = [f"ответ не по схеме: {error}"]
            logger.warning("%s: модель не вернула JSON (%s)", subject, error)
            continue
        sentences = [sentence.strip() for sentence in drafted if sentence.strip()]
        text = " ".join(sentences)
        reasons = (
            barrier_problems(
                text, data, labels=labels, forbid_markers=forbid_markers, max_chars=max_chars
            )
            if text
            else ["пустой текст"]
        )
        if reasons:
            logger.warning("%s: текст модели не прошёл барьер: %s", subject, reasons)
            continue
        try:
            check = await check_facts(
                llm,
                sentences,
                data,
                prompt=prompt,
                model=checker_model,
                reasoning_effort=checker_reasoning_effort,
                evidence=evidence,
            )
        except httpx.HTTPError:
            logger.warning("%s по шаблону: проверяльщик недоступен", subject, exc_info=True)
            return CheckedText(fallback(), verified=False)
        if check.ok:
            return CheckedText(text, verified=True)
        reasons = check.reasons
        logger.warning("%s: проверяльщик отклонил текст: %s", subject, reasons)
    logger.warning("%s по шаблону: %d попыток не дали проверенного текста", subject, attempts)
    return CheckedText(fallback(), verified=False)
