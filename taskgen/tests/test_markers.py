"""Маркеры данных в промптах LLM: один формат для генерации и оценки."""

from __future__ import annotations

import pytest

from taskgen.markers import find_nonce, markers, new_nonce


def test_pair_shares_nonce_and_nonce_is_found_back():
    nonce = new_nonce()
    start, end = markers("ПРОФИЛЬ", nonce, end_label="КОНЕЦ-ПРОФИЛЯ")
    assert (start, end) == (f"<<<ПРОФИЛЬ-{nonce}>>>", f"<<<КОНЕЦ-ПРОФИЛЯ-{nonce}>>>")
    assert find_nonce("ПРОФИЛЬ", f"текст\n{start}\nданные\n{end}") == nonce
    assert find_nonce("ОТВЕТ", start) is None
    assert new_nonce() != nonce


def test_label_outside_format_is_refused():
    with pytest.raises(ValueError):
        markers("ответ кандидата", new_nonce())
