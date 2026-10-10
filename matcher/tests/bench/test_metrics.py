"""Метрики бенчмарка подбора; ожидаемые значения посчитаны вручную по формулам."""

from math import log2

import pytest

from tests.bench.metrics import below, ndcg_at, recall


def test_ndcg_matches_hand_computed_value() -> None:
    labels = {"a": 0, "b": 3, "c": 2}
    # DCG: позиция 1 — 0, позиция 2 — (2^3 - 1) / log2(3), позиция 3 — (2^2 - 1) / log2(4).
    # Идеальный порядок b, c, a: 7 / log2(2) + 3 / log2(3).
    expected = (7 / log2(3) + 3 / 2) / (7 + 3 / log2(3))
    assert ndcg_at(["a", "b", "c"], labels, k=10) == pytest.approx(expected)


def test_ndcg_of_ideal_order_is_one() -> None:
    labels = {"a": 3, "b": 2, "c": 2, "d": 0}
    assert ndcg_at(["a", "c", "b", "d"], labels, k=10) == pytest.approx(1.0)


def test_ndcg_skips_candidates_without_label() -> None:
    # Кандидаты вне набора (чужие профили в пуле) не занимают позиций.
    labels = {"a": 3, "b": 1}
    assert ndcg_at(["x", "a", "y", "b"], labels, k=10) == pytest.approx(1.0)


def test_ndcg_cuts_ranking_at_k() -> None:
    labels = {"a": 0, "b": 3}
    # На позиции 2 при k = 1 кандидат b не виден: DCG@1 = 0.
    assert ndcg_at(["a", "b"], labels, k=1) == 0.0


def test_ndcg_counts_relevant_candidate_missing_from_ranking_as_lost() -> None:
    labels = {"a": 3, "b": 2}
    # Идеальный DCG учитывает b, которого в выдаче нет: 3 / log2(3) теряется.
    expected = 7 / (7 + 3 / log2(3))
    assert ndcg_at(["a"], labels, k=10) == pytest.approx(expected)


def test_ndcg_is_undefined_without_relevant_candidates() -> None:
    assert ndcg_at(["a", "b"], {"a": 0, "b": 0}, k=10) is None


def test_recall_is_share_of_relevant_in_top() -> None:
    labels = {"a": 3, "b": 0, "c": 2, "d": 1}
    # Релевантные (не ниже 2) — a и c; в top есть только a.
    assert recall(["a", "b"], labels, threshold=2) == pytest.approx(0.5)


def test_recall_ignores_unlabelled_candidates_in_top() -> None:
    labels = {"a": 3, "c": 2}
    assert recall(["x", "a", "c"], labels, threshold=2) == pytest.approx(1.0)


def test_recall_is_undefined_without_relevant_candidates() -> None:
    assert recall(["a"], {"a": 1}, threshold=2) is None


def test_below_compares_positions() -> None:
    assert below(["ideal", "bad"], "bad", "ideal") is True
    assert below(["bad", "ideal"], "bad", "ideal") is False


def test_below_treats_missing_candidate_as_lowest() -> None:
    assert below(["ideal"], "bad", "ideal") is True
    assert below(["bad"], "bad", "ideal") is False
