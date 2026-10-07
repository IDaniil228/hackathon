"""Числа шлюза банка шаблонов для docs/taskgen/validation.md.

    uv run python scripts/gate_stats.py

Прогоняет проверку импорта (src/taskgen/bank/validation.py) по шаблонам с кодом из
seed/templates и считает, а не только утверждает: сколько кейсов дал генератор, сколько раз
верные решения прошли все кейсы и сколько неверных решений поймано. Нужен Piston по адресу
TASKGEN_SANDBOX__PISTON_URL; БД не нужна.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from taskgen.bank.parser import parse_template
from taskgen.bank.validation import TemplateValidator
from taskgen.config import Settings
from taskgen.contracts import TaskKind
from taskgen.sandbox.piston import PistonRunner

SEED_DIR = Path(__file__).resolve().parents[1] / "seed" / "templates"


async def main() -> int:
    settings = Settings()
    piston = PistonRunner(settings.sandbox)
    validator = TemplateValidator.from_settings(piston, settings)
    seeds = list(range(settings.import_check.seeds))
    specs = [
        (path.relative_to(SEED_DIR).as_posix(), parse_template(path.read_text("utf-8"), source=""))
        for path in sorted(SEED_DIR.rglob("*.md"))
    ]
    code = [(source, spec) for source, spec in specs if spec.kind is TaskKind.CODE]
    rows = []
    totals = {"cases": 0, "runs": 0, "correct": 0, "correct_ok": 0, "wrong": 0, "caught": 0}
    try:
        for source, spec in code:
            counts, correct, correct_ok, wrong, caught = [], 0, 0, 0, 0
            for seed in seeds:
                result = await validator.check_seed(spec, seed)
                counts.append(len(result.cases))
                correct += len(result.correct)
                correct_ok += sum(result.correct.values())
                wrong += len(result.caught)
                caught += sum(result.caught.values())
            spread = (
                str(min(counts)) if min(counts) == max(counts) else f"{min(counts)}–{max(counts)}"
            )
            rows.append(
                f"| `{source}` | `{spec.runtime}` | {spread} "
                f"| {correct_ok} из {correct} | {caught} из {wrong} |"
            )
            totals["cases"] += sum(counts)
            totals["runs"] += len(seeds)
            totals["correct"] += correct
            totals["correct_ok"] += correct_ok
            totals["wrong"] += wrong
            totals["caught"] += caught
    finally:
        await piston.aclose()

    essays = sum(spec.kind is TaskKind.ESSAY for _, spec in specs)
    print("| Шаблон | Окружение | Кейсов на seed | Верные прошли | Неверных поймано |")
    print("|---|---|---|---|---|")
    print("\n".join(rows))
    print()
    print(f"Шаблонов с кодом: {len(code)}, эссе: {essays}, seed: {seeds}")
    print(f"Экземпляров: {totals['runs']}, кейсов всего: {totals['cases']}")
    print(f"Верные решения прошли все кейсы: {totals['correct_ok']} из {totals['correct']}")
    print(f"Неверных решений поймано: {totals['caught']} из {totals['wrong']}")
    ok = totals["correct_ok"] == totals["correct"] and totals["caught"] == totals["wrong"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
