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


def canonical_language(language: str | None) -> str | None:
    """Каноническое имя или None, если язык сервису неизвестен."""
    if language is None:
        return None
    return _ALIASES.get(language.strip().lower())


def source_language(language: str | None) -> str | None:
    """Язык исходника: имя языка или окружения; None — сервис такого не знает.

    Окружение пишется на языке своего драйвера (python-ml — на python), а окружение без
    драйвера — на языке пакета Piston (bash). Ответ кандидата сверяется с окружением задания
    через canonical_language: там python-ml остаётся отдельным окружением.
    """
    if language is None:
        return None
    name = language.strip().lower()
    if (known := canonical_language(name)) is not None:
        return known
    if name not in _registry().names():
        return None
    spec = _registry().get(name)
    return canonical_language(spec.driver) or canonical_language(spec.language) or spec.language
