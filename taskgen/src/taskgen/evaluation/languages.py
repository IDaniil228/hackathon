"""Канонические имена языков: песочница, фронтенд и шаблоны называют их по-разному."""

from __future__ import annotations

_ALIASES = {
    "python": "python",
    "python3": "python",
    "py": "python",
    "cpp": "cpp",
    "c++": "cpp",
    "cxx": "cpp",
    "g++": "cpp",
}


def canonical_language(language: str | None) -> str | None:
    """Каноническое имя или None, если язык сервису неизвестен."""
    if language is None:
        return None
    return _ALIASES.get(language.strip().lower())
