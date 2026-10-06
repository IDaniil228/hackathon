from taskgen.evaluation.detector import LLMAIDetector
from tests.evaluation.fakes import FakeLLM


async def test_detector_returns_probability_and_names_itself():
    llm = FakeLLM(['{"probability": 0.8, "note": "однородный стиль"}'])
    signal = await LLMAIDetector(llm, model="judge").detect("print(1)", "python")
    assert signal.probability == 0.8
    assert signal.detector == "llm:judge"
    assert llm.calls[0]["model"] == "judge"


async def test_detector_failure_is_a_signal_without_probability():
    llm = FakeLLM(["мусор", "мусор"])
    signal = await LLMAIDetector(llm, model="judge").detect("print(1)", "python")
    assert signal.probability is None
    assert signal.note


async def test_detector_rejects_probability_out_of_range():
    llm = FakeLLM(['{"probability": 7, "note": "x"}', '{"probability": 7, "note": "x"}'])
    signal = await LLMAIDetector(llm, model="judge").detect("print(1)", "python")
    assert signal.probability is None
