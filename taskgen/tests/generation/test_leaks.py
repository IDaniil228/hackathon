"""Проверка условия: скрытые кейсы не попадают в текст, имя функции упомянуто."""

import pytest

from taskgen.contracts import TestCase
from taskgen.generation.leaks import hidden_leaks, mentions_entrypoint, public_count

PUBLIC = [TestCase(input=[[1, 2, 3], 5], expected=[1, 2])]
HIDDEN = [
    TestCase(input=[[10, 20, 30, 40], 70], expected=[2, 3]),
    TestCase(input={"text": "секретная строка"}, expected="ответ-секрет"),
]


def test_clean_statement_has_no_leaks():
    text = "Склад хранит отсортированные веса. Например, для [1, 2, 3] и 5 ответ [1, 2]."
    assert hidden_leaks(text, PUBLIC, HIDDEN) == []


@pytest.mark.parametrize(
    "fragment",
    [
        "[10, 20, 30, 40]",
        "[10,20,30,40]",
        "[ 10 , 20, 30,\n 40 ]",
        "'секретная строка'",
        "ответ-секрет",
        '{"text": "секретная строка"}',
    ],
)
def test_hidden_argument_or_expected_in_text_is_a_leak(fragment):
    assert hidden_leaks(f"Подсказка: {fragment}.", PUBLIC, HIDDEN)


def test_short_values_are_not_fingerprints():
    # Значения короче порога встречаются в тексте случайно: число 70 — не утечка.
    hidden = [TestCase(input=[70], expected=1)]
    assert hidden_leaks("Не больше 70 элементов, ответ 1.", PUBLIC, hidden) == []


def test_value_shared_with_public_example_is_not_a_leak():
    hidden = [TestCase(input=[[1, 2, 3], 6], expected=[])]
    assert hidden_leaks("Пример: [1, 2, 3].", PUBLIC, hidden) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Напишите функцию two_sum(nums, target).", True),
        ("Функция `two_sum` возвращает пару.", True),
        ("Напишите функцию two_sums.", False),
        ("Напишите функцию my_two_sum.", False),
        ("Напишите функцию.", False),
    ],
)
def test_mentions_entrypoint(text, expected):
    assert mentions_entrypoint(text, "two_sum") is expected


@pytest.mark.parametrize(
    ("total", "limit", "public"),
    [(1, 2, 1), (2, 2, 1), (3, 2, 2), (10, 2, 2), (10, 1, 1), (10, 0, 0)],
)
def test_public_count_keeps_at_least_one_hidden_case(total, limit, public):
    assert public_count(total, limit) == public
