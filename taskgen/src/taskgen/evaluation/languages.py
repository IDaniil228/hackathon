"""Канонические имена языков: песочница, фронтенд и шаблоны называют их по-разному."""

from __future__ import annotations

from functools import cache

from taskgen.sandbox.registry import RuntimeRegistry

_ALIASES = {
    "python": "python",
    "python3": "python",
    "py": "python",
    "cpp": "cpp",
    "c++": "cpp",
    "cxx": "cpp",
    "g++": "cpp",
}


@cache
def _runtime_drivers() -> dict[str, str]:
    # Окружение вроде python-ml пишется на языке своего драйвера, а драйверы названы
    # каноническими именами: реестр окружений — единственный список таких окружений.
    registry = RuntimeRegistry.load()
    drivers = {name: registry.get(name).driver for name in registry.names()}
    return {name: driver for name, driver in drivers.items() if driver is not None}


def canonical_language(language: str | None) -> str | None:
    """Каноническое имя или None, если язык сервису неизвестен."""
    if language is None:
        return None
    return _ALIASES.get(language.strip().lower())


def source_language(language: str | None) -> str | None:
    """Язык исходника: окружение вроде python-ml даёт язык своего драйвера.

    Только для выбора анализаторов и рубрики. Ответ сверяется с окружением задания через
    canonical_language, и там python-ml остаётся отдельным окружением.
    """
    if language is None:
        return None
    name = language.strip().lower()
    return canonical_language(name) or canonical_language(_runtime_drivers().get(name))
