"""Числа шлюза банка шаблонов для docs/taskgen/validation.md.

    uv run python scripts/gate_stats.py

Повторяет проверки tests/seed/test_seed_templates.py теми же функциями и считает, а не
только утверждает: сколько кейсов дал генератор, прошёл ли эталон и сколько неверных
решений поймано. Нужен Piston по адресу TASKGEN_SANDBOX__PISTON_URL; БД не нужна.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.seed.test_seed_templates import (  # noqa: E402
    CODE_PATHS,
    ESSAY_PATHS,
    SEEDS,
    _source,
    _spec,
    generate_cases,
    run_solution,
    wrong_solutions,
)

from taskgen.config import Settings  # noqa: E402
from taskgen.sandbox.piston import PistonRunner  # noqa: E402


async def main() -> int:
    piston = PistonRunner(Settings().sandbox)
    rows = []
    totals = {"cases": 0, "runs": 0, "reference": 0, "wrong": 0, "caught": 0}
    try:
        for path in CODE_PATHS:
            spec = _spec(path)
            counts, reference_ok, wrong, caught = [], 0, 0, 0
            for seed in SEEDS:
                cases = await generate_cases(piston, spec, seed)
                counts.append(len(cases))
                report = await run_solution(piston, spec, spec.reference_solution or "", cases)
                reference_ok += report.compile_error is None and report.pass_rate == 1.0
                for solution in wrong_solutions(spec, cases).values():
                    result = await run_solution(piston, spec, solution, cases)
                    wrong += 1
                    caught += result.compile_error is None and result.pass_rate < 1.0
            spread = (
                str(min(counts)) if min(counts) == max(counts) else f"{min(counts)}–{max(counts)}"
            )
            rows.append(
                f"| `{_source(path)}` | `{spec.runtime}` | {spread} "
                f"| {reference_ok} из {len(SEEDS)} | {caught} из {wrong} |"
            )
            totals["cases"] += sum(counts)
            totals["runs"] += len(SEEDS)
            totals["reference"] += reference_ok
            totals["wrong"] += wrong
            totals["caught"] += caught
    finally:
        await piston.aclose()

    print("| Шаблон | Окружение | Кейсов на seed | Эталон прошёл | Неверных поймано |")
    print("|---|---|---|---|---|")
    print("\n".join(rows))
    print()
    print(f"Шаблонов с кодом: {len(CODE_PATHS)}, эссе: {len(ESSAY_PATHS)}, seed: {list(SEEDS)}")
    print(f"Экземпляров: {totals['runs']}, кейсов всего: {totals['cases']}")
    print(f"Эталон прошёл все кейсы: {totals['reference']} из {totals['runs']}")
    print(f"Неверных решений поймано: {totals['caught']} из {totals['wrong']}")
    ok = totals["reference"] == totals["runs"] and totals["caught"] == totals["wrong"]
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
