"""Клиент настоящих моделей для smoke-тестов: пропуск теста, если провайдер роли недоступен."""

from __future__ import annotations

import httpx
import pytest

from taskgen.config import LLMSettings
from taskgen.llm import ProviderRouter


async def live_router(settings: LLMSettings, roles: tuple[str, ...]) -> ProviderRouter:
    """Маршрут к провайдерам ролей roles с долгим таймаутом или pytest.skip.

    Модель с рассуждениями отвечает дольше таймаута по умолчанию, поэтому таймаут — 600 с.
    """
    for name in sorted({settings.roles()[role].provider for role in roles}):
        provider = settings.providers[name]
        if provider.requires_key and provider.api_key is None:
            pytest.skip(f"у провайдера {name} нет ключа {settings.key_variable(name)}")
        headers = {}
        if provider.api_key is not None:
            headers["Authorization"] = f"Bearer {provider.api_key.get_secret_value()}"
        try:
            async with httpx.AsyncClient(timeout=3, headers=headers) as http:
                (await http.get(f"{provider.base_url.rstrip('/')}/models")).raise_for_status()
        except httpx.HTTPError:
            pytest.skip(f"провайдер {name} недоступен по адресу {provider.base_url}")
    return ProviderRouter.from_settings(settings.with_timeout(600), roles=roles)
