"""Прогон набора ответов tests/evaluation/adversarial через настоящую оценку.

    uv run python scripts/adversarial_eval.py --out results.json [--instances cache.json]
        [--only SET[:ANSWER,...]] [--judge-model PROVIDER/NAME --judge-reasoning-effort none]

Экземпляр каждого шаблона собирается generate_instance с моделью генерации из настроек и
песочницей Piston, ответ с кодом проходит тот же check_code, что в API, а балл считает
evaluate_task с LLM-судьёй. Судья — роль llm.judge из настроек, например
TASKGEN_LLM__JUDGE__MODEL=deepseek/deepseek-flash; флаги --judge-* заменяют её модель вместе с
провайдером и рассуждения. Способ доказательства и повторы судьи тоже берутся из настроек,
например TASKGEN_SCORING__JUDGE_EVIDENCE=spans.
Экземпляры кэшируются в --instances: повторный прогон и прогон другим судьёй оценивают те
же условия.

    uv run python scripts/adversarial_eval.py --summary results.json [другие.json ...]

печатает сводку прогонов: баллы честных ответов и атак, вердикты, снятые из-за неверного
доказательства, повторы судьи и время.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import sys
import time
import uuid
from pathlib import Path
from statistics import fmean
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.evaluation.adversarial.dataset import AnswerSet, check, load_sets  # noqa: E402

from taskgen.config import LLMSettings, Settings  # noqa: E402
from taskgen.contracts import (  # noqa: E402
    ChatMessage,
    HiddenPayload,
    RunLimits,
    TaskKind,
)
from taskgen.evaluation.analyzers import default_analyzers  # noqa: E402
from taskgen.evaluation.evidence import split_spans  # noqa: E402
from taskgen.evaluation.jsonout import extract_json_object  # noqa: E402
from taskgen.evaluation.judge import JudgeError, LLMJudge  # noqa: E402
from taskgen.evaluation.quotes import EmbeddingCache  # noqa: E402
from taskgen.evaluation.service import (  # noqa: E402
    AnswerInput,
    Evaluator,
    TaskInput,
    check_code,
    evaluate_task,
    judge_evidence,
    judge_relevance,
)
from taskgen.generation.instance import (  # noqa: E402
    GenerationError,
    InstanceDeps,
    generate_instance,
)
from taskgen.llm import ProviderRouter  # noqa: E402
from taskgen.sandbox.piston import PistonRunner  # noqa: E402


class CountingLLM:
    """LLMClient, который считает вызовы: бюджет облачного провайдера ограничен."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls = 0
        # Сырые ответы судьи до проверки цитат: по ним видно, какие вердикты сняла проверка.
        self.replies: list[str] = []

    async def complete(self, messages: list[ChatMessage], **kwargs: Any) -> str:
        self.calls += 1
        try:
            reply = await self.inner.complete(messages, **kwargs)
        except Exception as error:
            self.replies.append(f"ошибка: {error!r}"[:500])
            raise
        self.replies.append(reply)
        return reply


def _selection(only: list[str]) -> dict[str, set[str] | None]:
    chosen: dict[str, set[str] | None] = {}
    for item in only:
        name, _, answers = item.partition(":")
        chosen[name] = set(answers.split(",")) if answers else None
    return chosen


async def _instance(
    answer_set: AnswerSet, deps: InstanceDeps, cache: dict[str, Any]
) -> dict[str, Any]:
    key = f"{answer_set.template}#{answer_set.seed}"
    if key not in cache:
        instance = await generate_instance(answer_set.spec(), answer_set.seed, deps)
        cache[key] = {
            "kind": instance.kind.value,
            "statement": instance.statement,
            "params": instance.params,
            "hidden": instance.hidden_dump(),
        }
    return cache[key]


def _essay_from_facts(answer_set: AnswerSet) -> dict[str, Any]:
    spec = answer_set.spec()
    return {
        "kind": TaskKind.ESSAY.value,
        "statement": answer_set.statement_from_facts(),
        "hidden": HiddenPayload(rubric=spec.rubric, scoring=spec.scoring).model_dump(mode="json"),
        "statement_source": "facts",
    }


async def _evaluate(
    ev: Evaluator, answer_set: AnswerSet, instance: dict[str, Any], content: str
) -> dict[str, Any]:
    kind = TaskKind(instance["kind"])
    hidden = HiddenPayload.model_validate(instance["hidden"])
    if content == "":
        # API отклоняет пустой content с ответом 422 до оценки.
        return {"score": 0.0, "rejected": "пустой content отклонён API"}
    test_report = analysis = None
    language = None
    if kind is TaskKind.CODE:
        language = hidden.runtime or ""
        test_report, analysis = await check_code(ev, hidden, content, language)
    spec = answer_set.spec()
    outcome = await evaluate_task(
        TaskInput(
            task_id=uuid.uuid4(),
            position=0,
            kind=kind,
            grade=spec.grade,
            statement=instance["statement"],
            hidden=hidden,
            answer=AnswerInput(
                content=content, language=language, test_report=test_report, analysis=analysis
            ),
        ),
        judge=ev.judge,
        scoring=ev.settings.scoring,
    )
    result: dict[str, Any] = {"score": outcome.score}
    if outcome.components is not None:
        result["components"] = outcome.components.model_dump()
    if outcome.tests is not None:
        result["tests"] = outcome.tests.model_dump()
    if outcome.judge is not None:
        result["judge"] = outcome.judge.model_dump()
    return result


def _referenced_spans(row: dict[str, Any]) -> list[tuple[int, int]]:
    """Число участков в каждой ссылке yes или partial из сырых ответов судьи и число
    участков ответа: по ним выбирается предел judge_max_spans."""
    total = len(split_spans(row["content"], code=row["kind"] == TaskKind.CODE.value))
    found = []
    for reply in row.get("replies", []):
        try:
            items = extract_json_object(reply).get("items", [])
        except ValueError:
            continue
        for item in items:
            if isinstance(item, dict) and item.get("verdict") in ("yes", "partial"):
                spans = item.get("spans")
                if isinstance(spans, list) and spans:
                    found.append((len(set(map(str, spans))), total))
    return found


def _quantile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


def summary(paths: list[Path]) -> None:
    for path in paths:
        rows = json.loads(path.read_text(encoding="utf-8"))
        scored = [row for row in rows if not math.isnan(row["score"])]
        honest = [row for row in scored if row["class"].startswith("valid-")]
        attacks = [row for row in scored if row["class"].startswith("inject-")]
        judged = [row["judge"] for row in scored if "judge" in row]
        claimed = sum(judge.get("claimed", 0) for judge in judged)
        unsupported = sum(judge.get("unsupported", 0) for judge in judged)
        below = [row for row in honest if any("ниже" in p for p in row["problems"])]
        above = [row for row in attacks if any("выше" in p for p in row["problems"])]
        evidence = rows[0].get("evidence", {}) if rows else {}
        print(f"{path}: {rows[0]['judge_model'] if rows else '-'} {evidence}")
        print(f"  ответов {len(rows)}, без оценки {len(rows) - len(scored)}")
        if honest:
            print(
                f"  честные: средний балл {fmean(r['score'] for r in honest):.3f}, "
                f"ниже диапазона {len(below)} из {len(honest)}"
            )
        if attacks:
            print(
                f"  атаки: средний балл {fmean(r['score'] for r in attacks):.3f}, "
                f"выше диапазона {len(above)} из {len(attacks)}"
            )
        share = unsupported / claimed if claimed else 0.0
        print(
            f"  вердиктов yes и partial {claimed}, снято из-за доказательства {unsupported} "
            f"({share:.1%}), повторов судьи {sum(j.get('repairs', 0) for j in judged)}"
        )
        seconds = [row.get("seconds", 0.0) for row in rows]
        print(
            f"  вызовов судьи {sum(row['judge_calls'] for row in rows)}, "
            f"время {sum(seconds):.0f} с, на ответ {fmean(seconds) if seconds else 0:.1f} с"
        )
        for name, group in (("честные", honest), ("атаки", attacks)):
            refs = [ref for row in group for ref in _referenced_spans(row)]
            if refs:
                counts = [count for count, _ in refs]
                shares = [count / total for count, total in refs if total]
                print(
                    f"  участков на ссылку, {name}: медиана {_quantile(counts, 0.5)}, "
                    f"90 % {_quantile(counts, 0.9)}, максимум {max(counts)}; доля ответа: "
                    f"медиана {_quantile(shares, 0.5):.2f}, 90 % {_quantile(shares, 0.9):.2f}, "
                    f"максимум {max(shares):.2f}"
                )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--instances", type=Path)
    parser.add_argument("--only", nargs="*", default=[])
    parser.add_argument("--judge-model")
    parser.add_argument("--judge-reasoning-effort")
    parser.add_argument("--summary", nargs="+", type=Path)
    args = parser.parse_args()
    if args.summary:
        summary(args.summary)
        return 0
    if args.out is None:
        parser.error("нужен --out или --summary")

    settings = Settings()
    judge_role = settings.llm.judge.model_copy(
        update={
            "model": args.judge_model or settings.llm.judge.model,
            "reasoning_effort": args.judge_reasoning_effort or settings.llm.judge.reasoning_effort,
        }
    )
    # Через validate, а не model_copy: новая модель судьи проверяется вместе с остальными.
    llm_settings = LLMSettings.model_validate(
        {**dict(settings.llm.with_timeout(600)), "judge": judge_role}
    )
    cache: dict[str, Any] = {}
    if args.instances and args.instances.exists():
        cache = json.loads(args.instances.read_text(encoding="utf-8"))

    router = ProviderRouter.from_settings(llm_settings)
    counting = CountingLLM(router)
    judge_model = judge_role.model
    judge = LLMJudge(
        counting,
        model=judge_model,
        samples=settings.scoring.judge_samples,
        temperature=settings.scoring.judge_temperature,
        reasoning_effort=judge_role.reasoning_effort,
        evidence=judge_evidence(settings.scoring),
        # Эмбеддинги для проверки смысла доказательств даёт роль llm.embedding из настроек.
        relevance=judge_relevance(settings.scoring, router, EmbeddingCache()),
    )
    piston = PistonRunner(settings.sandbox)
    ev = Evaluator(
        settings=settings,
        sessionmaker=None,  # type: ignore[arg-type]
        llm=router,
        judge=judge,
        sandbox=piston,
        analyzers=default_analyzers(settings.scoring.analyzer_timeout_seconds),
        detector=None,
    )
    deps = InstanceDeps(
        llm=router,
        sandbox=piston,
        model=llm_settings.generator.model,
        options=settings.generation,
        limits=RunLimits(
            timeout_ms=settings.sandbox.run_timeout_ms,
            memory_bytes=settings.sandbox.run_memory_limit_bytes,
        ),
        reasoning_effort=llm_settings.generator.reasoning_effort,
    )

    chosen = _selection(args.only)
    rows: list[dict[str, Any]] = []
    try:
        for answer_set in load_sets():
            if chosen and answer_set.name not in chosen:
                continue
            try:
                instance = await _instance(answer_set, deps, cache)
            except GenerationError as error:
                print(f"{answer_set.name}: экземпляр не собран — {error}", flush=True)
                if answer_set.spec().kind is not TaskKind.ESSAY:
                    continue
                # Судье эссе нужны только условие и рубрика: ситуация собирается из фактов.
                # Условие из фактов тоже идёт в кэш: следующий прогон оценивает то же условие.
                instance = cache[f"{answer_set.template}#{answer_set.seed}"] = _essay_from_facts(
                    answer_set
                )
                print(f"{answer_set.name}: условие собрано из фактов шаблона", flush=True)
            if args.instances:
                args.instances.write_text(
                    json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8"
                )
            scores: dict[str, float] = {}
            wanted = chosen.get(answer_set.name)
            for answer in answer_set.answers:
                if wanted is not None and answer.id not in wanted:
                    continue
                before = counting.calls
                started = time.perf_counter()
                try:
                    result = await _evaluate(ev, answer_set, instance, answer.content)
                except JudgeError as error:
                    # Судья без ответа по схеме срывает оценку всей сессии: это тоже итог.
                    result = {"score": float("nan"), "error": str(error)}
                seconds = time.perf_counter() - started
                if "error" in result:
                    problems = [f"оценки нет: {result['error']}"]
                else:
                    scores[answer.id] = result["score"]
                    problems = list(check(answer, result["score"], scores).problems)
                row = {
                    "set": answer_set.name,
                    "answer": answer.id,
                    "class": answer.cls,
                    "expect": list(answer.expect),
                    "judge_model": judge_model,
                    "evidence": vars(judge_evidence(settings.scoring))
                    | {"quote_threshold": settings.scoring.judge_quote_threshold},
                    "kind": instance["kind"],
                    "content": answer.content,
                    "seconds": seconds,
                    "judge_calls": counting.calls - before,
                    "replies": counting.replies[before:],
                    "problems": problems,
                    **result,
                }
                rows.append(row)
                spread = result.get("judge", {}).get("spread")
                print(
                    f"{answer_set.name:38} {answer.id:18} {answer.cls:18} "
                    f"{result['score']:.3f} {answer.expect} spread={spread} "
                    f"{'; '.join(problems) or 'ok'}",
                    flush=True,
                )
                args.out.write_text(
                    json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8"
                )
    finally:
        await piston.aclose()
        await router.aclose()
    print(f"Вызовов судьи: {counting.calls}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
