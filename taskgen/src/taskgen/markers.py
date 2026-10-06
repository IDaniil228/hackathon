"""Маркеры, которыми промпты LLM отделяют данные от инструкций.

Данные (ответ кандидата, его профиль, ситуация на проверку) стоят между строками вида
<<<МЕТКА-nonce>>> и <<<КОНЕЦ-nonce>>>. nonce случаен в каждом вызове, поэтому данные не могут
заранее содержать закрывающий маркер и выйти из своего раздела. Формат задан здесь один раз
для всех промптов сервиса.
"""

from __future__ import annotations

import re
import secrets

# Метка — слова из заглавных букв через дефис: ОТВЕТ, КОНЕЦ-ПРОФИЛЯ.
_LABEL = r"[А-ЯЁA-Z]+(?:-[А-ЯЁA-Z]+)*"
_NONCE_BYTES = 8


def new_nonce() -> str:
    return secrets.token_hex(_NONCE_BYTES)


def marker(label: str, nonce: str) -> str:
    if re.fullmatch(_LABEL, label) is None:
        raise ValueError(f"Метка маркера {label!r} не в формате сервиса")
    return f"<<<{label}-{nonce}>>>"


def markers(label: str, nonce: str, end_label: str = "КОНЕЦ") -> tuple[str, str]:
    """Открывающий и закрывающий маркеры раздела данных с одной меткой вызова."""
    return marker(label, nonce), marker(end_label, nonce)


def find_nonce(label: str, text: str) -> str | None:
    """nonce открывающего маркера с меткой label в тексте или None."""
    found = re.search(re.escape(f"<<<{label}-") + r"([0-9a-f]+)>>>", text)
    return found.group(1) if found else None
