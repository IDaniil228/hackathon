"""Карточка профиля и её текст для эмбеддинга, один шаблон для обеих сторон подбора.

Кандидат и потребность описываются одними полями: потребность — как карточка идеального
кандидата. Тогда векторы сравнивают тексты одного вида. Негативный промт хранится в карточке
для LLM-стадии, но в текст не попадает никогда: bge-m3 принимает отрицание за факт, и
"без опыта с базой" притягивает профили с базой (docs/taskgen/models.md).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class SkillLevel:
    name: str
    # Уровень 0..5 из profile.candidate_skills или min_level потребности; None — не задан.
    level: int | None = None

    def render(self) -> str:
        return self.name if self.level is None else f"{self.name} (уровень {self.level} из 5)"


@dataclass(frozen=True)
class Card:
    category: str | None = None
    # True — категория подтверждена тестом, False — заявлена или импортирована.
    category_confirmed: bool | None = None
    confirmed_skills: tuple[SkillLevel, ...] = ()
    claimed_skills: tuple[SkillLevel, ...] = ()
    experience_months: int | None = None
    about: str | None = None
    positive_prompt: str | None = None
    negative_prompt: str | None = None
    github: tuple[str, ...] = ()
    fsp: tuple[str, ...] = ()
    summary: str | None = None


def _category(card: Card) -> str | None:
    if not card.category:
        return None
    if card.category_confirmed is None:
        return card.category
    status = "подтверждена тестом" if card.category_confirmed else "не подтверждена тестом"
    return f"{card.category} ({status})"


def _skills(skills: tuple[SkillLevel, ...]) -> str | None:
    return ", ".join(skill.render() for skill in skills) or None


def _clean(text: str | None) -> str | None:
    # Перевод строки внутри поля сломал бы разбор карточки по строкам "Поле: значение".
    return " ".join(text.split()) or None if text else None


def render_profile(card: Card) -> str:
    """Текст карточки: строки "Поле: значение" в постоянном порядке, пустые поля пропущены."""
    fields = [
        ("Категория", _category(card)),
        ("Подтверждённые навыки", _skills(card.confirmed_skills)),
        ("Заявленные навыки", _skills(card.claimed_skills)),
        (
            "Опыт работы",
            f"{card.experience_months} мес." if card.experience_months is not None else None,
        ),
        ("О себе", _clean(card.about)),
        ("Пожелания", _clean(card.positive_prompt)),
        ("GitHub", "; ".join(card.github) or None),
        ("ФСП", "; ".join(card.fsp) or None),
        ("Результаты тестов", _clean(card.summary)),
    ]
    return "\n".join(f"{name}: {value}" for name, value in fields if value)


def source_hash(text: str) -> str:
    """Ключ кэша вектора: SHA-256 текста, который ушёл в эмбеддер."""
    return hashlib.sha256(text.encode()).hexdigest()
