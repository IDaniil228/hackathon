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
