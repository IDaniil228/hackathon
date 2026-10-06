"""Описание окружения песочницы для кандидата: "Окружение: …".

Текст собирается из реестра (runtimes.yaml) и файла зависимостей пакета, поэтому список
библиотек и версии нигде не набираются руками второй раз.
"""

from __future__ import annotations

from functools import lru_cache

from taskgen.sandbox.registry import RuntimeRegistry

STANDARD_LIBRARY_ONLY = "только стандартная библиотека"


@lru_cache
def _default_registry() -> RuntimeRegistry:
    return RuntimeRegistry.load()


def describe_runtime(name: str, registry: RuntimeRegistry | None = None) -> str:
    registry = registry or _default_registry()
    spec = registry.get(name)
    title = spec.description.format(version=spec.version, standard=spec.standard or "")
    libraries = registry.libraries(name)
    if libraries:
        extra = "библиотеки " + ", ".join(f"{lib} {version}" for lib, version in libraries)
    else:
        extra = STANDARD_LIBRARY_ONLY
    return f"Окружение: {title}; {extra}"


def describe_all(registry: RuntimeRegistry | None = None) -> dict[str, str]:
    """Описания всех окружений реестра: для автодокументации API."""
    registry = registry or _default_registry()
    return {name: describe_runtime(name, registry) for name in registry.names()}
