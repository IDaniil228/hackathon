"""Размеченный набор бенчмарка проверки эссе: разметка следует из построения и не устаревает."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from collections import Counter
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "essay_check_bench.py"
_spec = importlib.util.spec_from_file_location("essay_check_bench", SCRIPT)
bench = importlib.util.module_from_spec(_spec)
# dataclass ищет модуль класса в sys.modules.
sys.modules[_spec.name] = bench
_spec.loader.exec_module(bench)

LEAKAGE = "ml-validation-leakage-essay.yaml"


def test_cases_json_is_built_from_sources():
    assert bench.CASES.read_text(encoding="utf-8") == bench.render_cases(bench.build_cases())


def test_every_essay_has_every_kind_of_variant():
    cases = bench.build_cases()
    kinds = {(case["essay"], case["kind"]) for case in cases}
    essays = {case["essay"] for case in cases}
    assert len(essays) == 9
    for essay in essays:
        for kind in ("good", "deletion", "paraphrase", "negation"):
            assert (essay, kind) in kinds
    assert Counter(case["kind"] for case in cases)["good"] == 18


def copy_sources(tmp_path: Path, edit) -> tuple[Path, Path]:
    essays = tmp_path / "essays"
    shutil.copytree(bench.BENCH / "essays", essays)
    data = yaml.safe_load((essays / LEAKAGE).read_text(encoding="utf-8"))
    edit(data)
    (essays / LEAKAGE).write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    fixtures = tmp_path / "fixtures.yaml"
    fixtures.write_text("[]\n", encoding="utf-8")
    return essays, fixtures


def test_paraphrase_sharing_a_word_with_its_phrase_is_rejected(tmp_path):
    def edit(data):
        data["paraphrases"][0]["text"] = "Позже нашли утечку сведений из проверочной части."

    with pytest.raises(bench.BenchDataError, match="дословна|общие слова"):
        bench.build_cases(*copy_sources(tmp_path, edit))


def test_paraphrase_sharing_a_word_form_is_rejected(tmp_path):
    # Подстрокой "разбиение по времени" здесь не найти, но слово "разбил" с фразой общее.
    def edit(data):
        data["paraphrases"][1] = {
            "phrases": ["разбиение по времени"],
            "text": "Коллега разбил данные по месяцам: ранние для обучения, поздние для проверки.",
        }

    with pytest.raises(bench.BenchDataError, match="общие слова"):
        bench.build_cases(*copy_sources(tmp_path, edit))


def test_good_situation_with_forbidden_phrase_is_rejected(tmp_path):
    def edit(data):
        data["situations"][0]["units"][5]["text"] = "Он обучил бустинг с регуляризацией."

    with pytest.raises(bench.BenchDataError, match="хорошей ситуации"):
        bench.build_cases(*copy_sources(tmp_path, edit))


def test_fact_carried_by_two_sentences_is_rejected(tmp_path):
    def edit(data):
        data["situations"][0]["units"][0]["facts"].append("boosting")

    with pytest.raises(bench.BenchDataError, match="ровно одно"):
        bench.build_cases(*copy_sources(tmp_path, edit))


def test_deletion_and_negation_labels_follow_construction():
    cases = {case["id"]: case for case in bench.build_cases()}
    good = cases["ml-validation-leakage-essay/good-1/good"]
    deleted = cases["ml-validation-leakage-essay/good-1/delete-3"]
    negated = cases["ml-validation-leakage-essay/good-1/negate-scale-all"]

    assert deleted["missing_facts"] == ["scale-all"]
    assert "привёл признаки к одному масштабу" not in deleted["situation"]
    assert negated["missing_facts"] == ["scale-all"] and negated["negated_fact"] == "scale-all"
    assert "только по обучающей части" in negated["situation"]
    assert good["missing_facts"] == [] and good["forbidden_found"] == []


def test_skipped_deletions_are_not_generated():
    ids = {case["id"] for case in bench.build_cases()}
    # small-range остаётся спорным без своего предложения, поэтому такого варианта нет.
    assert "algo-sorting-stability-essay/good-1/delete-2" not in ids
    assert "algo-sorting-stability-essay/good-1/delete-3" in ids


def test_llm_sample_keeps_kind_shares_and_fixture():
    cases = bench.build_cases()

    sample = bench.llm_sample(cases, 40, seed=7)

    kinds = Counter(case["kind"] for case in cases if case["id"] in set(sample))
    assert kinds["fixture"] == 1
    assert all(kinds[kind] >= 1 for kind in bench.KINDS)
    assert sample == bench.llm_sample(cases, 40, seed=7)


def test_split_is_by_essay():
    cases = bench.build_cases()
    tune, test = bench.split_essays(cases)
    assert not set(tune) & set(test)
    assert set(tune) | set(test) == {case["essay"] for case in cases}


def test_negation_error_counts_negated_fact_judged_present():
    cases = {case["id"]: case for case in bench.build_cases()}
    cid = "ml-validation-leakage-essay/good-1/negate-scale-all"
    fooled = {
        "case_id": cid,
        "missing_facts": [],
        "forbidden_found": [],
        "seconds": 1.0,
        "llm_calls": 0,
    }
    right = dict(fooled, missing_facts=["scale-all"])

    assert bench.evaluate([fooled], cases)["negation_error"] == (1, 1)
    assert bench.evaluate([right], cases)["negation_error"] == (0, 1)
    assert bench.evaluate([right], cases)["fact"] == (1.0, 1.0, 1.0)


# --- Замер смысла цитат -----------------------------------------------------------------------


def test_quote_pairs_cover_every_rubric_item_of_every_essay():
    pairs = bench.quote_pairs()
    honest = {(p["essay"], p["target"]) for p in pairs if p["kind"] == "honest"}
    aware = {(p["essay"], p["target"]) for p in pairs if p["kind"] == "aware"}
    templates = sorted(bench.TEMPLATES.glob("*/*/*-essay.md"))
    expected = {
        (spec.name, item.id)
        for spec in (bench.load_spec(str(path.relative_to(bench.TEMPLATES))) for path in templates)
        for item in spec.rubric
    }
    assert len(templates) == 9
    assert honest == expected == aware
    assert {p["kind"] for p in pairs if p["group"] == "fact"} == {"carrier", "other", "negation"}


def test_quote_file_without_honest_quotes_for_an_item_is_rejected(tmp_path):
    quotes = tmp_path / "quotes"
    shutil.copytree(bench.QUOTES, quotes)
    path = quotes / "ml-validation-leakage-essay.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    del data["honest"]["remedies"]
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    with pytest.raises(bench.BenchDataError, match="честные цитаты"):
        bench.quote_pairs(quotes)


def test_quote_report_safe_threshold_keeps_every_honest_quote():
    pairs = [
        {"group": group, "kind": kind}
        for group, honest in bench.HONEST.items()
        for kind in (honest, honest, "attack", "attack")
    ]
    scores = [0.5, 0.6, 0.4, 0.7] * 2

    report = bench.quote_report(pairs, scores)

    for group in bench.HONEST:
        assert report[group]["safe_threshold"] == 0.5
        assert report[group]["shares"]["0.5"] == {bench.HONEST[group]: 0.0, "attack": 0.5}
        assert report[group]["shares"]["0.51"][bench.HONEST[group]] == 0.5
