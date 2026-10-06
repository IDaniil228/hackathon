import math
import random
from collections import Counter

import pytest

from taskgen.bank.selection import (
    cosine_similarity,
    distance,
    draw,
    grade_positions,
    jaccard_similarity,
    probabilities,
)

# Критическое значение хи-квадрат для 4 степеней свободы при уровне значимости 0.001.
CHI2_CRIT_DF4 = 18.467


def chi_square(counts: Counter, expected: list[float], total: int) -> float:
    return sum((counts[i] - total * p) ** 2 / (total * p) for i, p in enumerate(expected))


def first_pick_counts(distances: list[float], t: float, n: int, seed: int) -> Counter:
    rng = random.Random(seed)
    return Counter(draw(distances, 1, t, rng)[0].index for _ in range(n))


def adjacent_gaps(levels: dict[str, int]) -> list[int]:
    positions = grade_positions(levels)
    ordered = sorted(levels, key=levels.__getitem__)
    return [positions[b] - positions[a] for a, b in zip(ordered, ordered[1:], strict=False)]


def test_grade_positions_follow_level_order_not_level_values():
    assert grade_positions({"senior": 30, "junior": 10, "middle": 20}) == {
        "junior": 0,
        "middle": 1,
        "senior": 2,
    }


def test_inserted_intermediate_grade_keeps_unit_step_between_neighbours():
    base = {"junior": 10, "middle": 20, "senior": 30}
    extended = {**base, "junior+": 15}
    assert adjacent_gaps(base) == [1, 1]
    assert adjacent_gaps(extended) == [1, 1, 1]
    positions = grade_positions(extended)
    # Шаг junior → junior+ равен шагу middle → senior, хотя разности level 5 и 10.
    assert positions["junior+"] - positions["junior"] == positions["senior"] - positions["middle"]


def test_distance_combines_grade_gap_and_dissimilarity():
    assert distance(0, 1.0, alpha=1.0, beta=1.0) == 0.0
    assert distance(2, 1.0, alpha=0.5, beta=1.0) == pytest.approx(1.0)
    assert distance(1, 0.25, alpha=1.0, beta=2.0) == pytest.approx(2.5)
    assert distance(-1, 1.0, alpha=1.0, beta=0.0) == pytest.approx(1.0)


def test_cosine_and_jaccard_similarity():
    assert cosine_similarity([1.0, 0.0], [2.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 3.0]) == pytest.approx(0.0)
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert jaccard_similarity({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
    assert jaccard_similarity(set(), set()) == 0.0


def test_probabilities_sum_to_one_and_prefer_closer():
    d = [0.2, 1.0, 0.5, 2.0]
    for t in (0.1, 0.3, 0.5, 0.9):
        p = probabilities(d, t)
        assert sum(p) == pytest.approx(1.0)
        assert p[0] > p[2] > p[1] > p[3]


def test_probabilities_flatten_as_temperature_grows():
    d = [0.0, 1.0, 2.0]

    def entropy(p):
        return -sum(x * math.log(x) for x in p if x > 0)

    values = [entropy(probabilities(d, t)) for t in (0.05, 0.3, 0.6, 0.95)]
    assert values == sorted(values)
    assert values[0] < values[-1]


def test_probabilities_at_extremes():
    d = [0.4, 0.1, 0.1, 3.0]
    assert probabilities(d, 0.0) == [0.0, 0.5, 0.5, 0.0]
    assert probabilities(d, 1.0) == [0.25] * 4


def test_probabilities_survive_huge_distances_at_low_temperature():
    p = probabilities([1000.0, 1001.0], 0.001)
    assert sum(p) == pytest.approx(1.0)
    assert p[0] > 0.99


@pytest.mark.parametrize("t", [-0.1, 1.1])
def test_temperature_outside_unit_interval_is_rejected(t):
    with pytest.raises(ValueError):
        probabilities([0.0], t)


def test_draw_is_without_replacement_and_capped_by_pool():
    rng = random.Random(1)
    picks = draw([0.1, 0.2, 0.3], 5, 0.5, rng)
    assert sorted(p.index for p in picks) == [0, 1, 2]


def test_draw_is_reproducible_by_seed():
    d = [0.1, 0.4, 0.4, 0.9, 1.3]
    first = [p.index for p in draw(d, 3, 0.4, random.Random(7))]
    second = [p.index for p in draw(d, 3, 0.4, random.Random(7))]
    assert first == second


def test_zero_temperature_takes_nearest_and_breaks_ties_randomly():
    d = [0.5, 0.1, 0.9, 0.1]
    firsts = set()
    for seed in range(50):
        picks = draw(d, 2, 0.0, random.Random(seed))
        assert {p.index for p in picks} == {1, 3}
        assert picks[0].probability == pytest.approx(0.5)
        firsts.add(picks[0].index)
    assert firsts == {1, 3}


def test_zero_temperature_takes_claimed_grade_then_neighbours():
    # Сходство одинаковое, поэтому порядок решает только удалённость грейда.
    gaps = [0, 0, 1, 1, 2, 2]
    d = [distance(g, 0.5, alpha=1.0, beta=1.0) for g in gaps]
    for seed in range(200):
        two = draw(d, 2, 0.0, random.Random(seed))
        assert sorted(gaps[p.index] for p in two) == [0, 0]
        three = draw(d, 3, 0.0, random.Random(seed))
        assert sorted(gaps[p.index] for p in three) == [0, 0, 1]


@pytest.mark.parametrize("t", [1.0, 0.99])
def test_high_temperature_is_close_to_uniform(t):
    d = [0.0, 0.5, 1.0, 1.5, 2.0]
    n = 5000
    counts = first_pick_counts(d, t, n, seed=20261006)
    assert chi_square(counts, [0.2] * 5, n) < CHI2_CRIT_DF4


def test_low_temperature_is_far_from_uniform():
    # Тот же критерий обязан различать неравномерный отбор, иначе проверка выше пустая.
    d = [0.0, 0.5, 1.0, 1.5, 2.0]
    n = 5000
    counts = first_pick_counts(d, 0.3, n, seed=20261006)
    assert chi_square(counts, [0.2] * 5, n) > CHI2_CRIT_DF4


def test_draw_frequencies_match_softmax_probabilities():
    d = [0.0, 0.3, 0.6, 1.2, 2.0]
    n = 5000
    counts = first_pick_counts(d, 0.4, n, seed=99)
    assert chi_square(counts, probabilities(d, 0.4), n) < CHI2_CRIT_DF4


def test_step_probability_is_conditional_on_remaining_pool():
    d = [0.0, 0.0, 5.0]
    picks = draw(d, 3, 1.0, random.Random(3))
    assert [p.probability for p in picks] == pytest.approx([1 / 3, 1 / 2, 1.0])
