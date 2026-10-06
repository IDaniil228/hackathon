"""Реестр окружений: имя runtime сервиса -> язык и версия пакета Piston.

Данные лежат в runtimes.yaml рядом с модулем, поэтому окружение добавляется без правки кода.
Код нужен только для нового языка обвязки, и поле harness ограничено языками, для которых
она написана.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ValidationError

from taskgen.contracts import RunLimits
from taskgen.sandbox.errors import SandboxError


class RuntimeSpec(BaseModel):
    language: str
    version: str
    harness: Literal["python", "cpp"]
    compile_memory_bytes: int | None = None
    # Пакет Piston, который даёт язык; один пакет может давать несколько языков.
    package: str | None = None
    # official ставится из репозитория Piston, local собирается из taskgen/sandbox/packages.
    source: Literal["official", "local"] = "official"
    # Пол лимитов окружения: тяжёлый рантайм не запускается в лимитах по умолчанию.
    min_timeout_ms: int | None = None
    min_memory_bytes: int | None = None

    @property
    def package_name(self) -> str:
        return self.package or self.language

    def effective_limits(self, limits: RunLimits) -> RunLimits:
        """Запрошенные лимиты, поднятые до пола окружения; бо́льшие запросы не меняются."""
        return RunLimits(
            timeout_ms=max(limits.timeout_ms, self.min_timeout_ms or 0),
            memory_bytes=max(limits.memory_bytes, self.min_memory_bytes or 0),
        )


class RuntimeRegistry:
    def __init__(self, runtimes: dict[str, RuntimeSpec]) -> None:
        self._runtimes = runtimes

    @classmethod
    def load(cls, path: Path | None = None) -> RuntimeRegistry:
        if path is None:
            text = resources.files(__package__).joinpath("runtimes.yaml").read_text("utf-8")
        else:
            text = path.read_text(encoding="utf-8")
        raw = yaml.safe_load(text) or {}
        runtimes = {}
        for name, fields in raw.items():
            try:
                runtimes[name] = RuntimeSpec.model_validate(fields)
            except ValidationError as error:
                raise SandboxError(f"окружение {name} описано неверно: {error}") from error
        return cls(runtimes)

    def get(self, name: str) -> RuntimeSpec:
        try:
            return self._runtimes[name]
        except KeyError:
            known = ", ".join(sorted(self._runtimes))
            raise SandboxError(f"неизвестное окружение {name}, известны: {known}") from None

    def names(self) -> list[str]:
        return sorted(self._runtimes)

    def packages(self) -> list[tuple[str, str, str]]:
        """Пакеты Piston, нужные окружениям: (source, package, version) без повторов."""
        return sorted(
            {(spec.source, spec.package_name, spec.version) for spec in self._runtimes.values()}
        )


if __name__ == "__main__":
    # Список читает скрипт установки пакетов, поэтому версии заданы только в runtimes.yaml.
    for source, package, version in RuntimeRegistry.load().packages():
        print(source, package, version)
