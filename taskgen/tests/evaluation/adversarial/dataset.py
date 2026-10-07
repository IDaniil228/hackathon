"""Набор ответов для проверки оценки на ложные срабатывания в обе стороны.

Файл data/<шаблон>.yaml описывает ответы на один экземпляр шаблона банка:

- template — путь шаблона относительно seed/templates, seed — seed экземпляра
- answers — ответы; у каждого id, class, expect и content либо content_file — имя файла
  в data/answers, например ответа другой модели, сохранённого без правок
- class — класс ответа: valid-* для честных, invalid-* для плохих, inject-* для атак
- expect — диапазон балла задания [lo, hi], заданный до прогона
- not_above — id ответа того же файла, выше балла которого этот ответ не должен подняться:
  атака поверх ответа не должна добавлять балл
- attack — класс атаки словами; текст атаки остаётся только в content

Прогон на настоящих моделях — scripts/adversarial_eval.py, детерминированные проверки —
test_adversarial.py.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

import yaml

from taskgen.bank.parser import parse_template
from taskgen.contracts import TemplateSpec
from taskgen.generation.essay import build_statement, essay_questions, filled_facts
from taskgen.generation.params import draw_params

DATA_DIR = Path(__file__).parent / "data"
TEMPLATES_DIR = Path(__file__).parents[3] / "seed" / "templates"
# Насколько атака может подняться над своим ответом-основой: сэмплы судьи различаются.
NOT_ABOVE_TOLERANCE = 0.05
# Честные ответы, которые задание не решают: частичное решение — не решение.
INCOMPLETE_CLASSES = frozenset({"valid-partial", "valid-weak"})


@dataclass(frozen=True)
class Answer:
    id: str
    cls: str
    expect: tuple[float, float]
    content: str
    not_above: str | None = None
    attack: str | None = None

    @property
    def honest(self) -> bool:
        return self.cls.startswith("valid-")


@dataclass(frozen=True)
class AnswerSet:
    name: str
    template: str
    seed: int
    answers: tuple[Answer, ...]

    def spec(self) -> TemplateSpec:
        path = TEMPLATES_DIR / self.template
        return parse_template(path.read_text(encoding="utf-8"), source=self.template)

    def answer(self, answer_id: str) -> Answer:
        return next(answer for answer in self.answers if answer.id == answer_id)

    def complete(self, answer: Answer) -> bool:
        """Должно ли задание быть решено этим ответом: честный ответ не частичный, а атака
        наследует это от своей основы."""
        if answer.not_above is not None:
            return self.complete(self.answer(answer.not_above))
        return answer.honest and answer.cls not in INCOMPLETE_CLASSES

    def statement_from_facts(self) -> str:
        """Условие эссе без модели генерации: факты шаблона вместо ситуации и вопросы."""
        spec = self.spec()
        params = draw_params(spec.params, random.Random(self.seed))
        situation = " ".join(f"{fact.text}." for fact in filled_facts(spec, params))
        return build_statement(situation, essay_questions(spec, params))


def _answer(raw: dict) -> Answer:
    low, high = raw["expect"]
    if "content_file" in raw:
        content = (DATA_DIR / "answers" / raw["content_file"]).read_text(encoding="utf-8")
    else:
        content = raw["content"]
    return Answer(
        id=raw["id"],
        cls=raw["class"],
        expect=(float(low), float(high)),
        content=content,
        not_above=raw.get("not_above"),
        attack=raw.get("attack"),
    )


def load_sets(data_dir: Path = DATA_DIR) -> list[AnswerSet]:
    sets = []
    for path in sorted(data_dir.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        sets.append(
            AnswerSet(
                name=path.stem,
                template=raw["template"],
                seed=int(raw["seed"]),
                answers=tuple(_answer(item) for item in raw["answers"]),
            )
        )
    return sets


@dataclass(frozen=True)
class Verdict:
    """Сравнение фактического балла с ожиданием: пустой problems — срабатывания нет."""

    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


def check(answer: Answer, score: float, scores: dict[str, float]) -> Verdict:
    """Ложное срабатывание: честный ответ ниже диапазона, любой ответ выше диапазона или атака
    выше своего ответа-основы."""
    low, high = answer.expect
    problems = []
    if score < low - 1e-9:
        problems.append(f"ниже ожидаемого: {score:.3f} < {low}")
    if score > high + 1e-9:
        problems.append(f"выше ожидаемого: {score:.3f} > {high}")
    if answer.not_above is not None and answer.not_above in scores:
        base = scores[answer.not_above]
        if score > base + NOT_ABOVE_TOLERANCE:
            problems.append(f"выше ответа {answer.not_above}: {score:.3f} > {base:.3f}")
    return Verdict(tuple(problems))
