"""Настройки генерации.

Поля предназначены для taskgen.config.Settings как поле generation; пока их там нет,
сервис берёт значения по умолчанию отсюда.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class GenerationSettings(BaseModel):
    # Сколько seed перебрать, пока эталон не пройдёт все кейсы экземпляра.
    instance_attempts: int = Field(default=5, ge=1)
    # Сколько раз попросить LLM переписать условие с утечкой или без имени функции.
    legend_attempts: int = Field(default=3, ge=1)
    # Легенда — творческий текст: при нулевой температуре экземпляры звучат одинаково.
    legend_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    public_examples: int = Field(default=2, ge=0)
