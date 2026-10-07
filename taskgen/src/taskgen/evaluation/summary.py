"""Machine summary кандидата: факты из оценённых сессий и текст по ним.

Факты собираются из результатов сессий по белому списку полей. Сигнал детектора ИИ, рубрики
и прочее содержимое hidden_payload в факты не попадают. Текст пишет LLM, а summary_problems
сверяет его с фактами: числа, пары "N из M", исходы тестов и грейды, оценочные ярлыки. Текст,
который не прошёл проверку, заменяется текстом по шаблону.
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
    "insufficient": "заданий заявленного уровня недостаточно для вердикта",
}
_KIND_TEXT = {"code": "код", "essay": "развёрнутый ответ", "quiz": "тестовая задачка"}

# Числительные словами проверяются до двадцати: так модели пишут число заданий уровня,
# задачек и тестов задания; большие числа они пишут цифрами.
_TEENS = (
    "одиннадцат",
    "двенадцат",
    "тринадцат",
    "четырнадцат",
    "пятнадцат",
    "шестнадцат",
    "семнадцат",
    "восемнадцат",
    "девятнадцат",
    "двадцат",
)
_NUMERAL_FORMS = {
    0: ("ноль", "нуля", "ни одного", "ни одной"),
    1: ("один", "одна", "одно", "одного", "одной", "одну", "одним", "одном"),
    2: ("два", "две", "двух", "двум"),
    3: ("три", "трёх", "трех", "трём"),
    4: ("четыре", "четырёх", "четырех"),
    5: ("пять", "пяти"),
    6: ("шесть", "шести"),
    7: ("семь", "семи"),
    8: ("восемь", "восьми"),
    9: ("девять", "девяти"),
    10: ("десять", "десяти"),
    **{11 + index: (stem + "ь", stem + "и") for index, stem in enumerate(_TEENS)},
}
_NUMERAL_WORDS = {form: value for value, forms in _NUMERAL_FORMS.items() for form in forms}
_PAIR_TOKEN = (
    r"(?:\d+(?:[.,]\d+)?|ни\s+одно(?:го|й)|"
    + "|".join(sorted(_NUMERAL_WORDS, key=len, reverse=True))
    + r")(?![\w.,]?\d)(?!\w)"
)
# "2 из 2", "0 правильных ответов из 1": между числом и "из" не больше двух слов.
_PAIR_FORWARD = re.compile(
    rf"(?<![\w.,])(?P<x>{_PAIR_TOKEN})\s+(?:[^\W\d][\w-]*\s+){{0,2}}?из\s+(?P<y>{_PAIR_TOKEN})",
    re.IGNORECASE,
)
_PAIR_SLASH = re.compile(r"(?<![\w.,/])(?P<x>\d+)\s*/\s*(?P<y>\d+)(?![\w.,/])")
# "из 2 задач по коду решён только 1": между "из M" и числом только слова, без знаков.
_PAIR_BACKWARD = re.compile(
    rf"(?<!\w)из\s+(?P<y>{_PAIR_TOKEN})\s+(?:[^\W\d][\w-]*\s+){{1,5}}?(?P<x>{_PAIR_TOKEN})",
    re.IGNORECASE,
)
# "Тест 1 из 2" — номер сессии из их числа, а не счёт решённого.
_SESSION_PREFIX = re.compile(r"(?<!\w)(?:тест[аеу]?|сесси[яюи])\s*№?\s*$", re.IGNORECASE)
_SESSION_NUMBER = re.compile(r"(?<!\w)(?:тест[аеу]?|сесси[яюи])\s*№?\s*(\d+)", re.IGNORECASE)
_ORDINALS = {"перв": 1, "втор": 2, "трет": 3, "четвёрт": 4, "четверт": 4, "пят": 5}
_SESSION_ORDINAL = re.compile(
    r"(?<!\w)(" + "|".join(_ORDINALS) + r")\w*\s+(?:тест|сесси)", re.IGNORECASE
)
# Тест пройден, когда подтверждён заявленный грейд или выше; при insufficient вердикта нет.
_TEST_PASSED = {"passed", "upgraded"}
_TEST_FAILED = {"failed", "downgraded"}
_NEGATIVE_CLAIMS = (
    (
        "test_failed",
        re.compile(
            r"(?<!\w)не\s+(?:прош[её]л|прошла|сдал\w*|сдан\w*|пройден(?!\w)|справил\w*\s+с\s+тест)"
            r"|(?<!\w)провал\w*",
            re.IGNORECASE,
        ),
    ),
    (
        "grade_not_given",
        re.compile(
            r"(?<!\w)не\s+(?:подтвержд|присво|выставлен|назначен|получил\w*\s+грейд)\w*",
            re.IGNORECASE,
        ),
    ),
)
# Глагол прохождения перед цифрой — счёт: "пройден 1 из 10 тестов", "прошёл 2 сессии".
_POSITIVE_CLAIMS = (
    (
        "test_passed",
        re.compile(
            r"(?<!\w)(?:прош[её]л|прошла|сдал\w*|сдан\w*|пройден|выдержал\w*)(?!\w)(?!\s+\d)",
            re.IGNORECASE,
        ),
    ),
    ("grade_given", re.compile(r"(?<!\w)(?:подтвержд|присво)|получил\w*.*грейд", re.IGNORECASE)),
    (
        "outcome_named",
        re.compile(r"(?<!\w)(?:" + "|".join(_OUTCOME_TEXT) + r")(?!\w)", re.IGNORECASE),
    ),
)

_SYSTEM_TEMPLATE = """\
Ты пишешь краткую справку о результатах технического теста кандидата для рекрутера.

Правила:
- пиши по-русски, от 3 до 6 предложений, каждое предложение — отдельный элемент sentences;
- каждое предложение называет факт: число из фактов или исход теста; других чисел в тексте нет;
- пары "N из M" бери из фактов как есть и пиши цифрами: сколько заданий уровня решено из
  скольких, сколько тестов задания пройдено из скольких, сколько задачек верно из скольких;
  других пар не составляй;
- если тестов несколько, в предложении об исходе или грейде называй номер теста словами
  "Тест N";
- исход теста (outcome) передавай так: {outcomes};
- слова "прошёл" и "сдал" пиши только об исходе passed или upgraded, о прогоне тестов задания
  пиши "пройдено N из M тестов";
- о присвоенном грейде пиши, только если assigned_grade не null, и называй его код; при null
  пиши, что грейд не присвоен;
- описывай только факты: баллы, решённые задания, прогон тестов, грейды, время и действия;
- поведенческие данные (вставки из буфера, потери фокуса, время) называй числами без выводов;
- не делай выводов о честности кандидата и об использовании ИИ, не давай оценочных ярлыков."""
_SYSTEM = _SYSTEM_TEMPLATE.format(
    outcomes="; ".join(f"{code} — {text}" for code, text in _OUTCOME_TEXT.items())
)

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
                "quiz": _quiz_facts(session.result),
                "tasks": [_task_facts(task) for task in session.result.get("tasks", [])],
            }
            for index, session in enumerate(sessions, start=1)
        ],
    }


def _quiz_facts(result: dict[str, Any]) -> dict[str, int] | None:
    """Сколько тестовых задачек верно из скольких; None — задачек в сессии не было."""
    block = (result.get("score") or {}).get("quiz")
    if not block:
        return None
    return {"correct": block["correct"], "total": block["total"]}


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


def _pair_value(token: str) -> int | None:
    """Целое из цифр или числительного; None — дробь, она парой "N из M" не считается."""
    token = re.sub(r"\s+", " ", token.lower())
    if token.isdigit():
        return int(token)
    if re.fullmatch(r"\d+(?:[.,]\d+)?", token):
        return None
    return _NUMERAL_WORDS[token]


def _pairs_in(sentence: str) -> list[tuple[int, int, bool]]:
    """Пары "N из M" и "N/M" предложения; третий элемент — пара стоит после слова "тест"."""
    pairs: list[tuple[int, int, bool]] = []
    rest = sentence
    for pattern in (_PAIR_FORWARD, _PAIR_SLASH):
        for match in pattern.finditer(rest):
            x, y = _pair_value(match["x"]), _pair_value(match["y"])
            if x is not None and y is not None:
                session = bool(_SESSION_PREFIX.search(rest[: match.start()]))
                pairs.append((x, y, session))
        rest = pattern.sub(lambda match: " " * len(match.group(0)), rest)
    # Обратный порядок "из 2 задач решён 1" ищется в том, что не разобрали прямые пары:
    # иначе "5 из 13 тестов, 2 вставки" дал бы ложную пару 2 из 13.
    for match in _PAIR_BACKWARD.finditer(rest):
        x, y = _pair_value(match["x"]), _pair_value(match["y"])
        if x is not None and y is not None:
            pairs.append((x, y, False))
    return pairs


def _known_pairs(data: dict[str, Any]) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    """Пары из фактов: счётные (уровни, тесты, задачки) и номера тестов из их числа."""
    counts: set[tuple[int, int]] = set()
    sessions = {(session["number"], data["sessions_count"]) for session in data["sessions"]}
    for session in data["sessions"]:
        counts |= {(level["solved"], level["total"]) for level in session["levels"]}
        if quiz := session.get("quiz"):
            counts.add((quiz["correct"], quiz["total"]))
        for task in session["tasks"]:
            if tests := task.get("tests"):
                counts.add((tests["passed"], tests["total"]))
    return counts, sessions


def _mentions(sentence: str, word: str | None) -> bool:
    return bool(word) and bool(
        re.search(rf"(?<!\w){re.escape(word)}(?!\w)", sentence, re.IGNORECASE)
    )


def _bound_sessions(sentence: str, data: dict[str, Any]) -> list[dict[str, Any]]:
    """Сессии, о которых говорит предложение; пустой список — привязать нельзя."""
    sessions = data["sessions"]
    numbers = {int(n) for n in _SESSION_NUMBER.findall(sentence)}
    numbers |= {_ORDINALS[stem.lower()] for stem in _SESSION_ORDINAL.findall(sentence)}
    if bound := [session for session in sessions if session["number"] in numbers]:
        return bound
    if bound := [session for session in sessions if _mentions(sentence, session["area"])]:
        return bound
    return sessions if len(sessions) == 1 else []


def _claims_in(sentence: str) -> list[str]:
    """Утверждения об исходе и грейде; отрицания вырезаются до поиска утверждений."""
    claims: list[str] = []
    rest = sentence
    for kind, pattern in _NEGATIVE_CLAIMS:
        if pattern.search(rest):
            claims.append(kind)
            rest = pattern.sub(" ", rest)
    claims += [kind for kind, pattern in _POSITIVE_CLAIMS if pattern.search(rest)]
    return claims


def _claim_holds(kind: str, session: dict[str, Any], sentence: str) -> bool:
    outcome, assigned = session["outcome"], session["assigned_grade"]
    if kind == "test_passed":
        return outcome in _TEST_PASSED
    if kind == "test_failed":
        return outcome in _TEST_FAILED
    if kind == "grade_given":
        return _mentions(sentence, assigned)
    if kind == "grade_not_given":
        return assigned is None
    # Исход назван значением из фактов: "итог — failed".
    return _mentions(sentence, outcome)


def _claim_problems(sentence: str, claims: list[str], data: dict[str, Any]) -> list[str]:
    sessions = _bound_sessions(sentence, data)
    if not sessions:
        return [f"утверждение об исходе без номера или области теста: {sentence!r}"]
    return [
        f"утверждение {kind} не совпадает с исходом теста {session['number']}: {sentence!r}"
        for kind in claims
        for session in sessions
        if not _claim_holds(kind, session, sentence)
    ]


def summary_problems(text: str, data: dict[str, Any]) -> list[str]:
    """Нарушения правил текста; пустой список — текст можно показывать.

    Предложение опирается на факты, если в нём есть число, пара "N из M" или утверждение
    об исходе либо грейде, и всё это сходится с data.
    """
    known = _numbers_in(data)
    counts, session_pairs = _known_pairs(data)
    problems: list[str] = []
    if label := _LABELS.search(text):
        problems.append(f"оценочный ярлык: {label.group(0)!r}")
    for sentence in filter(None, (s.strip() for s in _SENTENCE_END.split(text.strip()))):
        numbers = [float(n.replace(",", ".")) for n in _NUMBER.findall(sentence)]
        pairs = _pairs_in(sentence)
        claims = _claims_in(sentence)
        if not numbers and not pairs and not claims:
            problems.append(f"предложение без фактов: {sentence!r}")
        for number in numbers:
            if not any(abs(number - value) < 1e-9 for value in known):
                problems.append(f"числа {number} нет в фактах: {sentence!r}")
        for x, y, session in pairs:
            if (x, y) not in (session_pairs if session else counts):
                problems.append(f"пары {x} из {y} нет в фактах: {sentence!r}")
        if claims:
            problems += _claim_problems(sentence, claims, data)
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
        if quiz := session.get("quiz"):
            sentences.append(
                f"Тест {session['number']}: тестовых задачек верно {quiz['correct']} из "
                f"{quiz['total']}."
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
