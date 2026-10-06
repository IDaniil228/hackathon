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

from taskgen.sandbox.errors import SandboxError


class RuntimeSpec(BaseModel):
    language: str
    version: str
    harness: Literal["python", "cpp"]
    compile_memory_bytes: int | None = None


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
