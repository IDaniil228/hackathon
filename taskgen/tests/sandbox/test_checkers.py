"""Встроенные режимы проверки вывода: сравнение в сервисе, без запуска кода."""

import json

import pytest

from taskgen.sandbox.checkers import output_matches, values_match


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


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("1 2 3\n", "1 2 3"),
        ("1\n2\n3", "1 2 3\n"),
        ("  a\tb  \r\n", "a b"),
        ("", "\n\n"),
    ],
)
def test_tokens_ignore_spaces_and_line_breaks(output, expected):
    assert output_matches("tokens", output, expected)


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("1 2", "1 2 3"),
        ("12 3", "1 23"),
        ("A", "a"),
        ("1.0", "1"),
        ("", "0"),
    ],
)
def test_tokens_compare_each_token_exactly(output, expected):
    assert not output_matches("tokens", output, expected)


def test_exact_compares_every_byte():
    assert output_matches("exact", "a b\n", "a b\n")
    assert not output_matches("exact", "a b", "a b\n")
    assert not output_matches("exact", "a  b\n", "a b\n")
    assert not output_matches("exact", "a b\r\n", "a b\n")


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("0.30000000000000004\n", "0.3"),
        ("1e12 x", "1000000000000.001 x"),
        ("2", "2.0"),
        ("ok 3.14159265", "ok\n3.141592650001"),
    ],
)
def test_float_compares_numbers_with_tolerance(output, expected):
    assert output_matches("float", output, expected)


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("0.31", "0.3"),
        ("1 2", "1 2 3"),
        ("ok 1", "OK 1"),
        ("nan", "0"),
        ("inf", "1e308"),
    ],
)
def test_float_rejects_other_numbers_and_words(output, expected):
    assert not output_matches("float", output, expected)


def test_json_compares_parsed_values():
    assert output_matches("json", '{"b": [1, 2.0000000001], "a": null}', '{"a":null,"b":[1,2]}')
    assert not output_matches("json", "[1, 2]", "[2, 1]")
    assert not output_matches("json", "true", "1")


def test_json_output_that_is_not_json_does_not_match():
    assert not output_matches("json", "[1, 2", json.dumps([1, 2]))
    assert not output_matches("json", "", "null")


def test_checker_mode_is_not_a_builtin_comparison():
    with pytest.raises(ValueError, match="checker"):
        output_matches("checker", "1", "1")
