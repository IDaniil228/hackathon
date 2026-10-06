"""Machine summary кандидата: факты из оценённых сессий и текст по ним.

Факты собираются из результатов сессий по белому списку полей. Сигнал детектора ИИ, рубрики
и прочее содержимое hidden_payload в факты не попадают. Текст пишет LLM, а код проверяет
его: каждое предложение содержит число из фактов, других чисел в тексте нет, оценочных
ярлыков про честность нет. Текст, который не прошёл проверку, заменяется текстом по шаблону.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel

from taskgen.contracts import ChatMessage, LLMClient, ReasoningEffort
from taskgen.evaluation.jsonout import StructuredOutputError, complete_structured

logger = logging.getLogger(__name__)

_TASK_FIELDS = (
    "position",
    "kind",
    "grade",
    "score",
    "solved",
    "answered",
    "language",
    "components",
)
_BEHAVIOR_FIELDS = ("paste_count", "focus_lost_count", "time_spent_sec")
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# Ярлыки, которые приписывают кандидату нечестность или авторство ИИ. Поведенческие
# сигналы допустимы только как числа: "3 вставки из буфера", а не вывод из них.
_LABELS = re.compile(
    r"списыв|списал|\bИИ\b|\bAI\b|нейросет|искусственн\w* интеллект|chatgpt|\bgpt|copilot"
    r"|читер|жульн|обман|плагиат|нечестн|мошен|подозрит|сгенерирова",
    re.IGNORECASE,
)
_OUTCOME_TEXT = {
    "passed": "заявленный грейд подтверждён",
    "failed": "ни один уровень не подтверждён",
    "upgraded": "подтверждён грейд выше заявленного",
    "downgraded": "подтверждён грейд ниже заявленного",
}
_KIND_TEXT = {"code": "код", "essay": "развёрнутый ответ"}

_SYSTEM = """Ты пишешь краткую справку о результатах технического теста кандидата для рекрутера.

Правила:
- пиши по-русски, от 3 до 6 предложений, каждое предложение — отдельный элемент sentences;
- каждое предложение содержит хотя бы одно число из фактов, и других чисел в тексте нет;
- описывай только факты: баллы, решённые задания, прогон тестов, грейды, время и действия;
- поведенческие данные (вставки из буфера, потери фокуса, время) называй числами без выводов;
- не делай выводов о честности кандидата и об использовании ИИ, не давай оценочных ярлыков."""

_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sentences"],
    "properties": {"sentences": {"type": "array", "items": {"type": "string"}}},
}


class SessionFacts(BaseModel):
    session_id: str
    area: str
    skills: list[str]
    scored_at: str
    result: dict[str, Any]


class _Sentences(BaseModel):
    sentences: list[str]


def _task_facts(task: dict[str, Any]) -> dict[str, Any]:
    facts = {name: task[name] for name in _TASK_FIELDS if name in task}
    if tests := task.get("tests"):
        facts["tests"] = {"passed": tests.get("passed"), "total": tests.get("total")}
    behavior = task.get("behavior") or {}
    facts["behavior"] = {name: behavior.get(name) for name in _BEHAVIOR_FIELDS}
    return facts


def summary_data(candidate_ref: str, sessions: list[SessionFacts]) -> dict[str, Any]:
    """Факты по всем оценённым сессиям кандидата; sessions идут от новой к старой."""
    return {
        "candidate_ref": candidate_ref,
        "sessions_count": len(sessions),
        "sessions": [
            {
                "number": index,
                "session_id": session.session_id,
                "area": session.area,
                "skills": session.skills,
                "scored_at": session.scored_at,
                "claimed_grade": session.result.get("claimed_grade"),
                "assigned_grade": session.result.get("assigned_grade"),
                "outcome": session.result.get("outcome"),
                "levels": [
                    {name: level.get(name) for name in ("grade", "total", "solved", "share")}
                    for level in session.result.get("levels", [])
                ],
                "tasks": [_task_facts(task) for task in session.result.get("tasks", [])],
            }
            for index, session in enumerate(sessions, start=1)
        ],
    }


def _numbers_in(data: Any) -> list[float]:
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
        return [number for value in data.values() for number in _numbers_in(value)]
    if isinstance(data, list):
        return [number for value in data for number in _numbers_in(value)]
    return []


def summary_problems(text: str, data: dict[str, Any]) -> list[str]:
    """Нарушения правил текста; пустой список — текст можно показывать."""
    known = _numbers_in(data)
    problems: list[str] = []
    if label := _LABELS.search(text):
        problems.append(f"оценочный ярлык: {label.group(0)!r}")
    for sentence in filter(None, (s.strip() for s in _SENTENCE_END.split(text.strip()))):
        numbers = [float(n.replace(",", ".")) for n in _NUMBER.findall(sentence)]
        if not numbers:
            problems.append(f"предложение без числа из фактов: {sentence!r}")
        for number in numbers:
            if not any(abs(number - value) < 1e-9 for value in known):
                problems.append(f"числа {number} нет в фактах: {sentence!r}")
    return problems


def _fmt(value: float) -> str:
    return f"{value:.2f}"


def fallback_text(data: dict[str, Any]) -> str:
    """Текст по шаблону: каждое предложение строится из полей data."""
    sentences: list[str] = []
    total = data["sessions_count"]
    for session in data["sessions"]:
        outcome = _OUTCOME_TEXT.get(session["outcome"], session["outcome"])
        assigned = session["assigned_grade"]
        sentences.append(
            f"Тест {session['number']} из {total}: область {session['area']}, "
            f"заявлен грейд {session['claimed_grade']}, {outcome}"
            + (f" ({assigned})." if assigned else ".")
        )
        for level in session["levels"]:
            sentences.append(
                f"Уровень {level['grade']}: решено {level['solved']} из {level['total']} заданий."
            )
        for task in session["tasks"]:
            line = (
                f"Задание {task['position']} ({_KIND_TEXT.get(task.get('kind'), task.get('kind'))}"
                f", {task.get('grade')}): балл {_fmt(task.get('score') or 0.0)}"
            )
            if task.get("tests") and task["tests"].get("total"):
                line += f", тестов пройдено {task['tests']['passed']} из {task['tests']['total']}"
            sentences.append(line + ".")
            behavior = task["behavior"]
            parts = []
            if behavior.get("paste_count") is not None:
                parts.append(f"вставок из буфера {behavior['paste_count']}")
            if behavior.get("focus_lost_count") is not None:
                parts.append(f"потерь фокуса {behavior['focus_lost_count']}")
            if behavior.get("time_spent_sec") is not None:
                parts.append(f"время работы {behavior['time_spent_sec']} с")
            if parts:
                sentences.append(f"Задание {task['position']}: " + ", ".join(parts) + ".")
    return " ".join(sentences)


async def write_summary(
    llm: LLMClient,
    data: dict[str, Any],
    *,
    model: str | None,
    reasoning_effort: ReasoningEffort | None = None,
) -> str:
    """Текст от LLM, если он прошёл проверку, иначе текст по шаблону."""
    messages = [
        ChatMessage(role="system", content=_SYSTEM),
        ChatMessage(
            role="user",
            content="Факты в JSON:\n" + json.dumps(data, ensure_ascii=False, indent=1),
        ),
    ]
    try:
        reply = await complete_structured(
            llm,
            messages,
            _Sentences,
            _SCHEMA,
            model=model,
            temperature=0.2,
            reasoning_effort=reasoning_effort,
        )
    except StructuredOutputError as error:
        logger.warning("Summary по шаблону: модель не вернула JSON (%s)", error)
        return fallback_text(data)
    text = " ".join(sentence.strip() for sentence in reply.sentences if sentence.strip())
    problems = summary_problems(text, data) if text else ["пустой текст"]
    if problems:
        logger.warning("Summary по шаблону: текст модели не прошёл проверку: %s", problems)
        return fallback_text(data)
    return text
