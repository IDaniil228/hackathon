import json

import pytest

from taskgen.evaluation.summary import (
    SessionFacts,
    fallback_text,
    summary_data,
    summary_problems,
    write_summary,
)
from tests.evaluation.fakes import FakeLLM

RESULT = {
    "claimed_grade": "middle",
    "assigned_grade": "junior",
    "outcome": "downgraded",
    "levels": [
        {"grade": "junior", "total": 2, "solved": 2, "share": 1.0},
        {"grade": "middle", "total": 3, "solved": 1, "share": 0.3333333333333333},
    ],
    "tasks": [
        {
            "position": 1,
            "kind": "code",
            "grade": "junior",
            "score": 0.82,
            "solved": True,
            "answered": True,
            "language": "python",
            "components": {"tests": 1.0, "rubric": 0.5, "quality": 0.71, "performance": 0.9},
            "tests": {"passed": 10, "total": 10},
            "behavior": {"paste_count": 3, "focus_lost_count": 7, "time_spent_sec": 640},
            "ai_signal": {"probability": 0.97},
        }
    ],
}


def _data() -> dict:
    session = SessionFacts(
        session_id="s1",
        area="algo",
        skills=["python"],
        scored_at="2026-10-06T12:00:00+00:00",
        result=RESULT,
    )
    return summary_data("cand-1", [session])


def test_data_carries_facts_but_not_ai_signal():
    data = _data()
    assert data["sessions"][0]["outcome"] == "downgraded"
    assert data["sessions"][0]["tasks"][0]["behavior"]["paste_count"] == 3
    assert "ai_signal" not in json.dumps(data)


def test_grounded_sentences_pass():
    text = "Решено 2 из 2 заданий junior. Пройдено 10 из 10 тестов, балл 0.82."
    assert summary_problems(text, _data()) == []


def test_sentence_without_number_is_rejected():
    problems = summary_problems("Кандидат хорошо знает алгоритмы.", _data())
    assert problems


def test_invented_number_is_rejected():
    problems = summary_problems("Решено 5 из 10 заданий.", _data())
    assert problems


def test_percent_form_of_share_is_accepted():
    assert summary_problems("Доля решённых заданий middle — 33%.", _data()) == []


@pytest.mark.parametrize(
    "label",
    [
        "Похоже, кандидат списывал: 3 вставки.",
        "Ответ написан ИИ, вставок 3.",
        "Вероятно, использовал ChatGPT: 3 вставки.",
        "Нечестное поведение: 7 потерь фокуса.",
    ],
)
def test_evaluative_labels_are_rejected(label):
    assert summary_problems(label, _data())


def test_fallback_text_is_itself_grounded():
    data = _data()
    text = fallback_text(data)
    assert text
    assert summary_problems(text, data) == []


def _task(position, kind, grade, score, tests=None, behavior=None):
    task = {
        "position": position,
        "kind": kind,
        "grade": grade,
        "score": score,
        "solved": score >= 0.7,
        "answered": True,
        "behavior": behavior or {"paste_count": 0, "focus_lost_count": 0, "time_spent_sec": None},
    }
    if tests:
        task["tests"] = {"passed": tests[0], "total": tests[1]}
    return task


def _session(number, area, claimed, assigned, outcome, levels, quiz, tasks):
    return {
        "number": number,
        "session_id": f"s{number}",
        "area": area,
        "skills": ["python"],
        "scored_at": "2026-10-07T18:40:00+00:00",
        "claimed_grade": claimed,
        "assigned_grade": assigned,
        "outcome": outcome,
        "levels": [
            {"grade": grade, "total": total, "solved": solved, "share": solved / total}
            for grade, solved, total in levels
        ],
        "quiz": quiz,
        "tasks": tasks,
    }


def _facts(*sessions):
    return {"candidate_ref": "cand", "sessions_count": len(sessions), "sessions": list(sessions)}


# Факты сквозного прогона: кандидат A провалил backend middle (тест 1) и подтвердил
# algo junior (тест 2), кандидат B провалил ml junior, кандидат C провалил backend senior.
FACTS_A = _facts(
    _session(
        1,
        "backend",
        "middle",
        None,
        "failed",
        [("middle", 0, 2)],
        {"correct": 0, "total": 1},
        [
            _task(1, "code", "middle", 0.0),
            _task(2, "essay", "middle", 0.0, behavior={"focus_lost_count": 1}),
        ],
    ),
    _session(
        2,
        "algo",
        "junior",
        "junior",
        "passed",
        [("junior", 2, 2)],
        {"correct": 1, "total": 1},
        [_task(1, "code", "junior", 0.94, tests=(17, 17)), _task(2, "essay", "junior", 1.0)],
    ),
)
FACTS_B = _facts(
    _session(
        1,
        "ml",
        "junior",
        None,
        "failed",
        [("junior", 0, 2)],
        {"correct": 0, "total": 1},
        [
            _task(1, "code", "junior", 0.33, tests=(5, 13)),
            _task(2, "code", "junior", 0.08, tests=(1, 10)),
        ],
    ),
)
FACTS_C = _facts(
    _session(
        1,
        "backend",
        "senior",
        None,
        "failed",
        [("senior", 1, 2)],
        {"correct": 1, "total": 1},
        [_task(1, "code", "senior", 0.89, tests=(12, 12)), _task(2, "essay", "senior", 0.0)],
    ),
)
# Одна сессия algo: задание с кодом решено на 0.95, но уровень middle не подтверждён.
FACTS_D = _facts(
    _session(
        1,
        "algo",
        "middle",
        None,
        "failed",
        [("middle", 1, 3)],
        None,
        [
            _task(1, "code", "middle", 0.95, tests=(9, 9)),
            _task(2, "code", "middle", 0.2, tests=(1, 8)),
            _task(3, "essay", "middle", 0.1),
        ],
    ),
)
# Исходы upgraded, downgraded и insufficient в трёх тестах одного кандидата.
FACTS_MIXED = _facts(
    _session(
        1,
        "backend",
        "junior",
        "middle",
        "upgraded",
        [("junior", 2, 2), ("middle", 1, 1)],
        None,
        [_task(1, "code", "junior", 0.9, tests=(10, 10))],
    ),
    _session(
        2,
        "ml",
        "senior",
        "middle",
        "downgraded",
        [("middle", 1, 1), ("senior", 0, 2)],
        {"correct": 3, "total": 5},
        [_task(1, "code", "senior", 0.4, tests=(4, 11))],
    ),
    _session(
        3,
        "algo",
        "middle",
        None,
        "insufficient",
        [("middle", 1, 1)],
        None,
        [_task(1, "essay", "middle", 0.8)],
    ),
)


@pytest.mark.parametrize(
    ("text", "facts"),
    [
        ("Кандидат не справился с заданием по backend (1 из 2 решённых, балл 0.0).", FACTS_A),
        ("Кандидат прошёл один алго-тест, решив задачу на 0.95 балла.", FACTS_D),
        ("Кодовые задачи: решён только один из двух заданий.", FACTS_B),
        ("В коде №1 решён 1 из 13 тестов.", FACTS_B),
        ("Кандидат прошёл тест по бэкенду, балл 0.89.", FACTS_C),
        ("По итогам теста кандидат получил senior-грейд, балл 0.89.", FACTS_C),
        (
            "Кандидат прошёл два технических теста: по бэкенду (backend) не сдано, по "
            "алгоритмам — успешно, баллы 0.0 и 0.94.",
            FACTS_A,
        ),
        (
            "Кандидат не справился с тестом: из 2 задач по коду решён только 1, балл — 0.33.",
            FACTS_B,
        ),
        ("В тесте по backend кандидат не решил ни одного задания из трёх.", FACTS_A),
        ("Тест 2 из 2: заявленный грейд junior не подтверждён.", FACTS_A),
        ("Тест 1 из 2: подтверждён грейд middle.", FACTS_A),
        ("Итог теста — passed, решено 0 из 2 заданий.", FACTS_B),
        ("Присвоен грейд junior, решено 2 из 2 заданий.", FACTS_A),
        ("Тест 3 из 3: решено 1 из 1 заданий, кандидат не прошёл тест.", FACTS_MIXED),
        ("Тест 1 из 3: подтверждён грейд senior.", FACTS_MIXED),
        ("Тесты задания 1 пройдены (1/13), балл 0.33.", FACTS_B),
        ("По коду решён один из двенадцати тестов, балл 0.89.", FACTS_C),
    ],
)
def test_false_claims_are_rejected(text, facts):
    assert summary_problems(text, facts)


@pytest.mark.parametrize(
    ("text", "facts"),
    [
        ("На уровне middle решено 0 из 2 заданий.", FACTS_A),
        ("Тест 2 из 2: algo, заявленный грейд junior подтверждён.", FACTS_A),
        ("Кандидат не прошёл технический тест: решён только один из двух заданий.", FACTS_C),
        ("Заявленный грейд junior, назначенный грейд не выставлен, итог сессии — failed.", FACTS_B),
        ("По итогам второй сессии присвоен грейд junior, результат — passed.", FACTS_A),
        ("Кандидат прошёл 2 сессии технического теста.", FACTS_A),
        ("В задании 2 на python набрано 0.08 балла, пройден 1 из 10 тестов.", FACTS_B),
        ("Кандидат не решил ни одного задания из 2 возможных.", FACTS_B),
        ("В задании по коду набрано 0.89 балла, тесты пройдены полностью (12/12).", FACTS_C),
        ("Во второй сессии код-задание выполнено с 17 пройденными тестами из 17.", FACTS_A),
        ("Тест 1 из 3: кандидат прошёл тест, подтверждён грейд middle.", FACTS_MIXED),
    ],
)
def test_true_claims_pass(text, facts):
    assert summary_problems(text, facts) == []


@pytest.mark.parametrize("facts", [FACTS_A, FACTS_B, FACTS_C, FACTS_D, FACTS_MIXED])
def test_fallback_text_passes_claim_checks(facts):
    assert summary_problems(fallback_text(facts), facts) == []


async def test_llm_text_used_when_grounded():
    sentences = ["Подтверждён грейд junior: решено 2 из 2 заданий.", "Вставок из буфера: 3."]
    llm = FakeLLM([json.dumps({"sentences": sentences}, ensure_ascii=False)])
    text = await write_summary(llm, _data(), model="gen")
    assert text == " ".join(sentences)
    prompt = "\n".join(message.content for message in llm.calls[0]["messages"])
    assert "0.97" not in prompt


async def test_llm_text_replaced_by_fallback_when_ungrounded():
    reply = json.dumps({"sentences": ["Кандидат явно списывал."]}, ensure_ascii=False)
    llm = FakeLLM([reply])
    data = _data()
    text = await write_summary(llm, data, model="gen")
    assert text == fallback_text(data)


async def test_llm_failure_falls_back():
    llm = FakeLLM(["не JSON", "снова не JSON"])
    data = _data()
    assert await write_summary(llm, data, model="gen") == fallback_text(data)
