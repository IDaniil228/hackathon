import pytest

from taskgen.sandbox.compare import values_match


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        (3, 3),
        (0.1 + 0.2, 0.3),
        (1e12 + 1e-3, 1e12),
        (2, 2.0),
        ([1, [2.0000000001, "a"]], [1, [2.0, "a"]]),
        ({"x": 1.0, "y": None}, {"y": None, "x": 1}),
        (True, True),
        ("строка", "строка"),
    ],
)
def test_equal_values_match(actual, expected):
    assert values_match(actual, expected)


@pytest.mark.parametrize(
    ("actual", "expected"),
    [
        (3, 4),
        (0.31, 0.3),
        (1.0, 1.0001),
        ([1, 2], [1, 2, 3]),
        ([1, 2], [2, 1]),
        ({"x": 1}, {"x": 1, "y": 2}),
        ({"x": 1}, {"y": 1}),
        # Логическое значение — не число, даже там, где Python считает True равным 1.
        (True, 1),
        (0, False),
        ("1", 1),
        (None, 0),
        ([1], 1),
        (float("nan"), 0.0),
    ],
)
def test_different_values_do_not_match(actual, expected):
    assert not values_match(actual, expected)
