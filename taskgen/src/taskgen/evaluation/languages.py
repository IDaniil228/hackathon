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
def _registry() -> RuntimeRegistry:
    return RuntimeRegistry.load()


def language_runtimes() -> list[str]:
    """Окружения, в которых решают задания; служебные окружения сервиса не входят."""
    registry = _registry()
    return [name for name in registry.names() if registry.get(name).kind == "language"]


def function_runtimes() -> list[str]:
    """Окружения, в которых доступны задачи io=function: для их языка написан драйвер."""
    return [name for name in language_runtimes() if _registry().get(name).functions]


def canonical_language(language: str | None) -> str | None:
    """Каноническое имя или None, если язык сервису неизвестен."""
    if language is None:
        return None
    return _ALIASES.get(language.strip().lower())


def source_language(language: str | None) -> str | None:
    """Язык исходника: имя языка или окружения; None — сервис такого не знает.

    Язык окружения — его поле code в реестре: python-ml пишется на python. Ответ кандидата
    сверяется с окружением задания через canonical_language: там python-ml остаётся
    отдельным окружением.
    """
    if language is None:
        return None
    name = language.strip().lower()
    if (known := canonical_language(name)) is not None:
        return known
    if name not in _registry().names():
        return None
    return _registry().get(name).code
