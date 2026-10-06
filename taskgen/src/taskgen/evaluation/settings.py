"""Настройки оценки, которых пока нет в taskgen.config.ScoringSettings.

Поля этой модели переезжают в ScoringSettings, когда его можно будет изменить. До этого
приложение может положить свой экземпляр в app.state.evaluation_settings.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class EvaluationSettings(BaseModel):
    # Балл, начиная с которого задание считается решённым. При весах по умолчанию 0.7
    # недостижим, если упала половина тестов: 0.6·0.5 + 0.4 = 0.7 только при идеальных
    # рубрике, качестве и скорости. Все тесты без замечаний судьи дают 0.6 и тоже не проходят.
    solved_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    # Ненулевая температура нужна, чтобы сэмплы судьи различались и разброс что-то значил.
    judge_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    analyzer_timeout_seconds: float = Field(default=30.0, gt=0.0)
    # Локальная модель отвечает на запросы по очереди; облачному провайдеру можно больше.
    llm_concurrency: int = Field(default=1, ge=1)
