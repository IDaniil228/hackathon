import json
import re
from pathlib import Path

import httpx
import pytest

from taskgen.config import EvidenceSettings
from taskgen.evaluation.factcheck import fact_lines
from taskgen.evaluation.summary import (
    SessionFacts,
    fallback_text,
    summary_data,
    summary_problems,
    write_summary,
)
from taskgen.llm import MissingAPIKeyError
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

# Факты и черновики сводки разных моделей с разметкой верности; их же гоняет живой набор.
DRAFTS = json.loads((Path(__file__).parent / "summary_drafts.json").read_text(encoding="utf-8"))


def _data() -> dict:
    session = SessionFacts(
        session_id="s1",
        area="algo",
        skills=["python"],
        scored_at="2026-10-06T12:00:00+00:00",
        result=RESULT,
    )
    return summary_data([session])


def test_data_carries_facts_but_not_ai_signal():
    data = _data()
    assert data["sessions"][0]["outcome"] == "downgraded"
    assert data["sessions"][0]["tasks"][0]["behavior"]["paste_count"] == 3
    assert "ai_signal" not in json.dumps(data)


def test_numbers_from_facts_pass():
    text = "Решено 2 из 2 заданий junior. Пройдено 10 из 10 тестов, балл 0.82."
    assert summary_problems(text, _data()) == []


@pytest.mark.parametrize(
    "text", ["Заявленный грейд middle не подтверждён.", "Кандидат работал в области algo."]
)
def test_sentence_without_number_passes_barrier(text):
    # Утверждение без числа сверяет проверяльщик, а не барьер кодом.
    assert summary_problems(text, _data()) == []


def test_invented_number_is_rejected():
    assert summary_problems("Решено 5 из 10 заданий.", _data())


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


@pytest.mark.parametrize("name", sorted(DRAFTS["facts"]))
def test_fallback_text_passes_barrier(name):
    data = DRAFTS["facts"][name]
    text = fallback_text(data)
    assert text
    assert summary_problems(text, data) == []


def test_fallback_text_of_service_facts_passes_barrier():
    data = _data()
    assert summary_problems(fallback_text(data), data) == []


def test_fact_lines_name_path_and_value():
    data = {"a": {"b": [1, {"c": None}]}, "e": [], "s": "x"}
    assert fact_lines(data) == ["a.b[0] = 1", "a.b[1].c = null", "e = []", 's = "x"']


SENTENCES = ["Подтверждён грейд junior: решено 2 из 2 заданий.", "Вставок из буфера: 3."]
# Строки фактов, которыми честный проверяльщик подтверждает каждое предложение SENTENCES.
PROOF = [
    [
        "sessions[0].assigned_grade",
        "sessions[0].levels[0].solved",
        "sessions[0].levels[0].total",
    ],
    ["sessions[0].tasks[0].behavior.paste_count"],
]
SPANS = EvidenceSettings(mode="spans", repairs=1)


def _span(data: dict, path: str) -> int:
    lines = fact_lines(data)
    return next(n for n, line in enumerate(lines, 1) if line.startswith(f"{path} = "))


def _verdict(data, number, paths, *, mode="spans", supported=True, reason=""):
    lines = fact_lines(data)
    if mode == "spans":
        proof = {"spans": [_span(data, path) for path in paths]}
    else:
        proof = {"quotes": [lines[_span(data, path) - 1] for path in paths]}
    return {"sentence": number, "supported": supported, "reason": reason, **proof}


class Models:
    """Писатель отвечает черновиками по порядку, последний повторяет; проверяльщик — ответами
    из очереди checker. sentences — один черновик или список черновиков."""

    def __init__(self, sentences: list, checker: list) -> None:
        self.drafts = sentences if sentences and isinstance(sentences[0], list) else [sentences]
        self.written = 0
        self.checker = iter(checker)

    def __call__(self, messages, schema):
        if "sentences" in schema["properties"]:
            draft = self.drafts[min(self.written, len(self.drafts) - 1)]
            self.written += 1
            return json.dumps({"sentences": draft}, ensure_ascii=False)
        reply = next(self.checker)
        if isinstance(reply, Exception):
            raise reply
        return json.dumps({"verdicts": reply}, ensure_ascii=False)


async def _write(llm, data, evidence=SPANS, attempts=1):
    return await write_summary(
        llm,
        data,
        model="gen",
        checker_model="chk",
        checker_reasoning_effort="low",
        evidence=evidence,
        attempts=attempts,
    )


def _writer_calls(llm) -> list:
    return [call for call in llm.calls if call["model"] == "gen"]


async def test_refused_draft_is_rewritten_with_the_checker_reasons():
    data = _data()
    wrong = ["Подтверждён грейд middle: решено 2 из 2 заданий.", SENTENCES[1]]
    refusal = [
        _verdict(data, 1, [], supported=False, reason="assigned_grade = junior, а не middle"),
        _verdict(data, 2, PROOF[1]),
    ]
    llm = FakeLLM(Models([wrong, SENTENCES], [refusal, _honest(data)]))

    assert await _write(llm, data, attempts=2) == " ".join(SENTENCES)

    second = _writer_calls(llm)[1]["messages"][-1].content
    assert "assigned_grade = junior, а не middle" in second


async def test_draft_stopped_by_the_barrier_is_rewritten_without_the_checker():
    data = _data()
    invented = ["Решено 5 из 10 заданий.", SENTENCES[1]]
    llm = FakeLLM(Models([invented, SENTENCES], [_honest(data)]))

    assert await _write(llm, data, attempts=2) == " ".join(SENTENCES)

    assert len(_writer_calls(llm)) == 2
    assert len([call for call in llm.calls if call["model"] == "chk"]) == 1
    assert "5" in _writer_calls(llm)[1]["messages"][-1].content


async def test_last_refused_attempt_falls_back_to_the_template():
    data = _data()
    refusal = [
        _verdict(data, 1, [], supported=False, reason="не следует из фактов"),
        _verdict(data, 2, PROOF[1]),
    ]
    llm = FakeLLM(Models(SENTENCES, [refusal, refusal, refusal]))

    assert await _write(llm, data, attempts=3) == fallback_text(data)
    assert len(_writer_calls(llm)) == 3


def _honest(data, mode="spans"):
    return [_verdict(data, n, paths, mode=mode) for n, paths in enumerate(PROOF, 1)]


async def test_confirmed_summary_is_shown():
    data = _data()
    llm = FakeLLM(Models(SENTENCES, [_honest(data)]))

    assert await _write(llm, data) == " ".join(SENTENCES)

    writer, checker = llm.calls
    assert "0.97" not in "\n".join(m.content for m in writer["messages"])
    assert (checker["model"], checker["reasoning_effort"]) == ("chk", "low")
    user = checker["messages"][-1].content
    facts = re.search(r"<<<ФАКТЫ-(\w+)>>>\n(.*?)\n<<<КОНЕЦ-ФАКТОВ-\1>>>", user, re.DOTALL)
    assert "sessions[0].outcome" in facts.group(2)
    # Идентификатор кандидата работодателю не нужен, а модели его пересказывают с ошибками.
    prompts = "\n".join(m.content for call in llm.calls for m in call["messages"])
    assert "cand-1" not in prompts and "candidate_ref" not in prompts
    text = re.search(r"<<<СПРАВКА-(\w+)>>>\n(.*?)\n<<<КОНЕЦ-СПРАВКИ-\1>>>", user, re.DOTALL)
    assert f"Предложение 2: {SENTENCES[1]}" in text.group(2)


async def test_confirmed_summary_with_quotes_is_shown():
    data = _data()
    llm = FakeLLM(Models(SENTENCES, [_honest(data, mode="quotes")]))
    evidence = EvidenceSettings(mode="quotes", repairs=0)
    assert await _write(llm, data, evidence) == " ".join(SENTENCES)


async def test_unsupported_sentence_falls_back(caplog):
    data = _data()
    refused = _verdict(data, 2, [], supported=False, reason="вставок 3, а не 4")
    llm = FakeLLM(Models(SENTENCES, [[_honest(data)[0], {**refused, "spans": []}]]))

    assert await _write(llm, data) == fallback_text(data)
    assert "вставок 3, а не 4" in caplog.text


@pytest.mark.parametrize(
    ("bad", "complaint"),
    [
        ({"spans": [999]}, "участков 999 нет"),
        ({"spans": []}, "нет номера участка"),
        # Строка существует, но в ней нет ни 2, ни 2 из "решено 2 из 2".
        ({"spans": [1]}, "нет строк с числами 2"),
    ],
)
async def test_invented_evidence_is_repaired_then_refused(bad, complaint):
    data = _data()
    wrong = [{**_honest(data)[0], **bad}, _honest(data)[1]]
    llm = FakeLLM(Models(SENTENCES, [wrong, wrong]))

    assert await _write(llm, data) == fallback_text(data)
    # Писатель, проверяльщик и один повтор с претензией: repairs=1.
    assert len(llm.calls) == 3
    assert complaint in llm.calls[-1]["messages"][-1].content


async def test_repaired_evidence_is_accepted():
    data = _data()
    wrong = [{**_honest(data)[0], "spans": [999]}, _honest(data)[1]]
    llm = FakeLLM(Models(SENTENCES, [wrong, _honest(data)]))
    assert await _write(llm, data) == " ".join(SENTENCES)


async def test_missing_verdict_falls_back():
    data = _data()
    llm = FakeLLM(Models(SENTENCES, [_honest(data)[:1], _honest(data)[:1]]))
    assert await _write(llm, data) == fallback_text(data)
    assert "нет вердикта по предложениям 2" in llm.calls[-1]["messages"][-1].content


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("сеть недоступна"),
        MissingAPIKeyError("У провайдера deepseek не задан ключ"),
    ],
)
async def test_unavailable_checker_falls_back(error, caplog):
    data = _data()
    llm = FakeLLM(Models(SENTENCES, [error]))
    assert await _write(llm, data) == fallback_text(data)
    assert "проверяльщик недоступен" in caplog.text


async def test_barrier_failure_skips_checker():
    data = _data()
    llm = FakeLLM(Models(["Кандидат явно списывал."], []))
    assert await _write(llm, data) == fallback_text(data)
    assert len(llm.calls) == 1


async def test_writer_failure_falls_back():
    llm = FakeLLM(["не JSON", "снова не JSON"])
    data = _data()
    assert await _write(llm, data) == fallback_text(data)
