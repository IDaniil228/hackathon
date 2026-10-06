"""Профиль кандидата: что основной бэкенд передаёт при создании сессии.

Профиль влияет на отбор шаблонов и на легенду, но не на сложность: параметры, эталон и тесты
задаются только шаблоном и seed. В сессии профиль живёт снимком, пока генерируются её
задания, а потом остаётся только хэш: по нему видно, что профиль кандидата изменился.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CandidateProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume: str = Field(default="", max_length=20000, description="Свободный текст резюме")
    skills: list[str] = Field(
        default=[],
        max_length=200,
        description="Коды навыков профиля из любых областей, а не только из тестируемой",
    )
    extra: dict[str, Any] = Field(
        default={}, description="Прочие поля профиля: опыт, роли, ссылки и так далее"
    )


def profile_hash(profile: CandidateProfile) -> str:
    """SHA-256 канонического JSON профиля: ключи по алфавиту, без пробелов, UTF-8.

    Порядок ключей в extra на хэш не влияет, порядок навыков и элементов списков — влияет.
    """
    canonical = json.dumps(
        profile.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
