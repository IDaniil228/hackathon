import pytest

from taskgen.contracts import AnalysisReport
from taskgen.evaluation.metrics import (
    compute_quality,
    judge_facts,
    load_rubric,
    parse_rubric,
)

RUBRIC = """# Метрики для теста

Размер решения: `tool.sloc`
Минимальный размер: 20

## `tool.cc_max`

Наибольшая сложность функции.
Вторая строка описания.

- хорошо: 5
- плохо: 15
- вес: 3

## `tool.style`

Замечания стиля.

- хорошо: 0
- плохо: 10
- нормировка: на 100 строк

## `tool.coverage`

Доля аннотированных мест: больше — лучше.

- хорошо: 1
- плохо: 0

## `tool.sloc`

Строки кода без пустых и комментариев.
"""


def _report(**metrics: float) -> AnalysisReport:
    return AnalysisReport(tool="tool", metrics=metrics)


def test_parse_reads_rules_and_descriptions():
    rubric = parse_rubric(RUBRIC)
    assert rubric.size_metric == "tool.sloc"
    assert rubric.min_size == 20
    cc = rubric.metrics["tool.cc_max"]
    assert (cc.good, cc.bad, cc.weight) == (5, 15, 3)
    assert "Вторая строка" in cc.description
    assert rubric.metrics["tool.style"].per_100_lines
    assert rubric.metrics["tool.sloc"].good is None


def test_parse_rejects_equal_good_and_bad():
    text = "# x\n\n## `a.b`\n\nОписание.\n\n- хорошо: 1\n- плохо: 1\n"
    with pytest.raises(ValueError):
        parse_rubric(text)


def test_parse_rejects_only_one_bound():
    text = "# x\n\n## `a.b`\n\nОписание.\n\n- хорошо: 1\n"
    with pytest.raises(ValueError):
        parse_rubric(text)


def test_parse_rejects_unknown_rule_key():
    text = "# x\n\n## `a.b`\n\nОписание.\n\n- хорошо: 1\n- плохо: 2\n- порог: 3\n"
    with pytest.raises(ValueError):
        parse_rubric(text)


def test_quality_interpolates_between_good_and_bad():
    rubric = parse_rubric(RUBRIC)
    result = compute_quality([_report(**{"cc_max": 10})], rubric)
    assert result.score == pytest.approx(0.5)


def test_quality_handles_metrics_where_more_is_better():
    rubric = parse_rubric(RUBRIC)
    result = compute_quality([_report(coverage=0.25)], rubric)
    assert result.score == pytest.approx(0.25)


def test_quality_clamps_beyond_bounds():
    rubric = parse_rubric(RUBRIC)
    assert compute_quality([_report(cc_max=1)], rubric).score == pytest.approx(1.0)
    assert compute_quality([_report(cc_max=99)], rubric).score == pytest.approx(0.0)


def test_quality_is_weighted_mean_over_present_metrics():
    rubric = parse_rubric(RUBRIC)
    result = compute_quality([_report(cc_max=15, coverage=1.0)], rubric)
    assert result.score == pytest.approx((3 * 0.0 + 1 * 1.0) / 4)


def test_per_100_lines_normalises_on_solution_size():
    rubric = parse_rubric(RUBRIC)
    # 5 замечаний на 200 строк = 2.5 на 100 строк, балл (10 - 2.5) / 10.
    result = compute_quality([_report(style=5, sloc=200)], rubric)
    assert result.score == pytest.approx(0.75)


def test_per_100_lines_uses_minimum_size_for_tiny_solutions():
    rubric = parse_rubric(RUBRIC)
    # 1 замечание на 5 строк считается как на 20 строк: 5 на 100, а не 20.
    result = compute_quality([_report(style=1, sloc=5)], rubric)
    assert result.score == pytest.approx(0.5)


def test_per_100_lines_without_size_is_skipped():
    rubric = parse_rubric(RUBRIC)
    assert compute_quality([_report(style=5)], rubric).score is None


def test_quality_without_rated_metrics_is_unknown():
    rubric = parse_rubric(RUBRIC)
    assert compute_quality([_report(sloc=10)], rubric).score is None
    assert compute_quality([], rubric).score is None


def test_metric_keys_are_prefixed_with_tool_name():
    rubric = parse_rubric(RUBRIC)
    other = AnalysisReport(tool="other", metrics={"cc_max": 99})
    assert compute_quality([other], rubric).score is None


def test_judge_facts_carry_value_and_meaning():
    rubric = parse_rubric(RUBRIC)
    facts = judge_facts([_report(cc_max=7, unknown=1)], rubric)
    by_key = {fact["метрика"]: fact for fact in facts}
    assert by_key["tool.cc_max"]["значение"] == 7
    assert "Наибольшая сложность" in by_key["tool.cc_max"]["смысл"]
    # Метрика без описания в рубрике тоже факт, но без трактовки.
    assert by_key["tool.unknown"]["смысл"] is None


@pytest.mark.parametrize("language", ["python", "cpp"])
def test_bundled_rubrics_parse(language):
    rubric = load_rubric(language)
    assert rubric is not None
    assert any(rule.good is not None for rule in rubric.metrics.values())
    assert rubric.size_metric in rubric.metrics


def test_language_aliases_resolve_to_the_same_rubric():
    assert load_rubric("c++") == load_rubric("cpp")
    assert load_rubric("python3") == load_rubric("python")
    assert load_rubric("brainfuck") is None
