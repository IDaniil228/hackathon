"""Бенчмарк подбора: прогоны матчера на наборе с релевантностью по построению.

Набор и его метки — tests/bench/dataset.py, метрики — tests/bench/metrics.py. Скрипт пишет
набор в базу api под владельцем таблиц, исполняет по прогону на каждую потребность тем же
RunService, что и сервис, под ролью matcher, считает метрики по match.search_results и
удаляет набор, даже если прогон упал.

    uv run python scripts/match_bench.py --variant hyde --k 3 --repeat 3
    uv run python scripts/match_bench.py --variant structured --k 3 --no-ranker
    uv run python scripts/match_bench.py --variant structured --k 3 --fake

Переменные окружения: MATCHER_DATABASE_URL — база под ролью matcher, как у сервиса;
MATCHER_TEST_OWNER_URL — та же база под владельцем таблиц api, как у интеграционных тестов.
Модели ролей и их reasoning_effort задают обычные переменные матчера, например
MATCHER_LLM__RANKER__MODEL; флаг --k заменяет только vector.top_k. --fake заменяет
эмбеддер детерминированной подделкой и выключает LLM-стадию: прогон проверяет механику без
моделей, а recall@K с ней не несёт смысла. В имени файла результата — вариант, K, модель
ранжировщика с reasoning_effort и модель эмбеддингов.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402
from tests.bench import dataset as ds  # noqa: E402
from tests.bench.metrics import below, ndcg_at, recall  # noqa: E402
from tests.conftest import FakeEmbedder  # noqa: E402

from matcher.config import Settings  # noqa: E402
from matcher.db import make_engine, make_sessionmaker  # noqa: E402
from matcher.explain import template_summary  # noqa: E402
from matcher.llm.contracts import Embedder  # noqa: E402
from matcher.llm.llm import ProviderRouter  # noqa: E402
from matcher.llm.llm_config import ChatRole  # noqa: E402
from matcher.llm_stage import LLMExplainer, LLMRanker, LLMRewriter  # noqa: E402
from matcher.ranking import Ranked, template_explanation  # noqa: E402
from matcher.runs import RunService  # noqa: E402
from matcher.stages import RunStages  # noqa: E402

RESULTS = ROOT / "tests" / "bench" / "results"
VARIANTS = ("structured", "hyde")
NO_RANKER = "none"
NDCG_K = 10
# recall@K считает кандидатов с меткой не ниже этой: идеальных и близких к ним.
RELEVANT = 2


class BenchError(Exception):
    """Бенчмарк не может начаться или оставил данные в базе."""


class CanonicalSummaries:
    """Источник machine summary вместо taskgen: канонические тексты набора."""

    def __init__(self, texts: Mapping[uuid.UUID, str | None]) -> None:
        self._texts = texts

    async def summary(self, candidate_ref: str) -> str | None:
        return self._texts.get(uuid.UUID(candidate_ref))


class CountingExplainer:
    """Объяснитель сервиса, который считает тексты, прошедшие проверяльщика.

    Отклонённый текст объяснитель заменяет шаблоном из строк факторов, поэтому текст, не
    равный шаблону, прошёл барьер и проверяльщика.
    """

    def __init__(self, inner: LLMExplainer) -> None:
        self._inner = inner
        self.reset()

    def reset(self) -> None:
        self.explained = self.checked = 0
        self.summary_checked: bool | None = None

    async def explain(self, item: Ranked) -> str:
        result = await self._inner.explain(item)
        self.explained += 1
        self.checked += result != template_explanation(item.grade_confirmed, item.factors)
        return result

    async def summarize(self, facts: dict[str, Any]) -> str:
        result = await self._inner.summarize(facts)
        self.summary_checked = result != template_summary(facts)
        return result


class UsageMeter:
    """Сумма поля usage ответов провайдеров: токены запроса, ответа и рассуждений."""

    def __init__(self) -> None:
        self.calls = 0
        self.prompt = self.completion = self.reasoning = 0

    def attach(self, router: ProviderRouter) -> None:
        # Клиенты провайдеров — внутренность маршрута; счётчик нужен только бенчмарку и
        # читает из ответа одно поле usage.
        for client in router._clients.values():
            client._http.event_hooks["response"].append(self._record)

    async def _record(self, response: httpx.Response) -> None:
        if not response.url.path.endswith("/chat/completions") or response.status_code != 200:
            return
        await response.aread()
        usage = response.json().get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        self.calls += 1
        self.prompt += usage.get("prompt_tokens") or 0
        self.completion += usage.get("completion_tokens") or 0
        self.reasoning += details.get("reasoning_tokens") or 0

    def as_json(self) -> dict[str, int]:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt,
            "completion_tokens": self.completion,
            "reasoning_tokens": self.reasoning,
        }


def role_json(role: ChatRole) -> dict[str, Any]:
    """Модель роли и её reasoning_effort; настройки провайдера с ключом сюда не попадают."""
    return {"model": role.model, "reasoning_effort": role.reasoning_effort}


def slug(model: str) -> str:
    """Короткое имя модели для имени файла: последняя часть пути без регистра и двоеточий."""
    return re.sub(r"[^a-z0-9._-]+", "-", model.rsplit("/", 1)[-1].lower())


def bench_settings(base: Settings, *, top_k: int) -> Settings:
    return base.model_copy(
        update={
            "vector": base.vector.model_copy(update={"top_k": top_k}),
            # Строки пула вне короткого списка — единственный след того, что префильтр пропустил.
            "results": base.results.model_copy(update={"store_pool": True}),
        }
    )


_RESULTS = text(
    """
    SELECT candidate_id, rank, stage::text AS stage, vector_score::float AS vector_score,
           llm_score::float AS llm_score, final_score::float AS final_score
    FROM match.search_results WHERE run_id = :run
    ORDER BY rank NULLS LAST, candidate_id
    """
)
_RUN = text(
    "SELECT status::text AS status, error_message, pool_size, params, llm_model "
    "FROM match.search_runs WHERE id = :run"
)


def evaluate(
    data: ds.Dataset, key: str, run: Mapping[str, Any], rows: list[Mapping[str, Any]]
) -> dict[str, Any]:
    """Метрики одного прогона по строкам match.search_results."""
    kinds = {c.id: c.kind for c in data.of(key)}
    labels = data.labels(key)
    params = run["params"] or {}
    ranking = [row["candidate_id"] for row in rows if row["rank"] is not None]
    pool = {row["candidate_id"] for row in rows}
    cut = {cid for cid, kind in kinds.items() if kind in ds.CUT}
    exploration = {
        uuid.UUID(cid)
        for part in ("fresh", "random")
        for cid in params.get("exploration", {}).get(part, [])
    }
    skipped = params.get("vector_skipped", True)
    top = None if skipped else [cid for cid in ranking if cid not in exploration]
    ideal = data.by_kind(key, ds.Kind.IDEAL)
    # Лучший порядок, который разрешает правило "неподтверждённый грейд ниже подтверждённых".
    best = sorted(data.of(key), key=lambda c: (c.grade_source != "test", -(c.relevance or 0)))
    return {
        "status": run["status"],
        "error": run["error_message"],
        "query_variant": params.get("query_variant"),
        "llm_model": run["llm_model"],
        "llm_fallback": params.get("llm_fallback"),
        "rewriter_error": params.get("rewriter_error"),
        "llm_error": params.get("llm_error"),
        "pool_size": run["pool_size"],
        "foreign_in_pool": len(pool - set(kinds)),
        "vector_skipped": skipped,
        "ndcg_at_10": ndcg_at(ranking, labels, k=NDCG_K),
        "ndcg_ceiling": ndcg_at([c.id for c in best], labels, k=NDCG_K),
        "recall_at_k": None if top is None else recall(top, labels, threshold=RELEVANT),
        "prefilter_ok": set(labels) <= pool and not cut & pool,
        "violator_below_ideal": below(ranking, data.by_kind(key, ds.Kind.VIOLATOR), ideal),
        "stuffer_below_ideal": below(ranking, data.by_kind(key, ds.Kind.STUFFER), ideal),
        "ranking": [
            {
                "rank": row["rank"],
                "kind": kinds[row["candidate_id"]].value
                if row["candidate_id"] in kinds
                else "foreign",
                "relevance": labels.get(row["candidate_id"]),
                "stage": row["stage"],
                "in_vector_top": None if top is None else row["candidate_id"] in top,
                "vector_score": row["vector_score"],
                "llm_score": row["llm_score"],
                "final_score": row["final_score"],
            }
            for row in rows
        ],
    }


def _mean(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return round(sum(present) / len(present), 4) if present else None


def summarize(needs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ndcg_at_10": _mean([n["ndcg_at_10"] for n in needs]),
        "ndcg_ceiling": _mean([n["ndcg_ceiling"] for n in needs]),
        "recall_at_k": _mean([n["recall_at_k"] for n in needs]),
        "prefilter_ok": f"{sum(n['prefilter_ok'] for n in needs)}/{len(needs)}",
        "violator_below_ideal": f"{sum(n['violator_below_ideal'] for n in needs)}/{len(needs)}",
        "stuffer_below_ideal": f"{sum(n['stuffer_below_ideal'] for n in needs)}/{len(needs)}",
        "runs_done": f"{sum(n['status'] == 'done' for n in needs)}/{len(needs)}",
        "llm_fallbacks": sum(bool(n["llm_fallback"]) for n in needs),
        "vector_skipped": f"{sum(n['vector_skipped'] for n in needs)}/{len(needs)}",
        "explanations_checked": _share(needs, "explanations_checked", "explanations"),
        "summaries_checked": _share(needs, "summary_checked", "summary_written"),
        "seconds": round(sum(n["seconds"] for n in needs), 1),
    }


def _share(needs: list[dict[str, Any]], part: str, whole: str) -> str | None:
    total = sum(n.get(whole) or 0 for n in needs)
    return f"{sum(n.get(part) or 0 for n in needs)}/{total}" if total else None


async def run_bench(
    settings: Settings,
    *,
    owner_url: str,
    variant: str,
    use_ranker: bool,
    explain: bool = False,
    embedder: Embedder | None = None,
    repeats: int = 1,
) -> dict[str, Any]:
    """Пишет набор, исполняет прогоны, считает метрики и удаляет набор, repeats раз.

    embedder заменяет эмбеддер из настроек; без него берётся роль embedding. explain
    включает объяснения выдачи с проверяльщиком; без ранжировщика их нет. Повтор пишет
    набор заново: кэш векторов и прогоны прошлого повтора на него не влияют, а разброс
    между повторами показывает недетерминизм моделей.
    """
    if variant not in VARIANTS:
        raise BenchError(f"вариант {variant!r} не поддержан: {', '.join(VARIANTS)}")
    explain = explain and use_ranker
    roles = {"embedding"} if embedder is None else set()
    roles |= {"rewriter"} if variant == "hyde" else set()
    roles |= {"ranker"} if use_ranker else set()
    roles |= {"checker"} if explain else set()
    router = ProviderRouter.from_settings(settings.llm, roles=roles) if roles else None
    usage = UsageMeter()
    if router is not None:
        usage.attach(router)
    explainer = CountingExplainer(LLMExplainer(router, settings)) if explain else None
    stages = RunStages(
        rewriter=LLMRewriter(router, settings) if variant == "hyde" else None,
        ranker=LLMRanker(router, settings) if use_ranker else None,
        explainer=explainer,
    )
    started = time.monotonic()
    owner_engine = make_engine(owner_url)
    engine = make_engine(settings.database_url)
    owner = make_sessionmaker(owner_engine)
    needs: list[dict[str, Any]] = []
    per_repeat = []
    try:
        for repeat in range(1, repeats + 1):
            async with owner() as session, session.begin():
                await ds.purge(session)
                data = ds.build(await ds.skill_names(session))
                await ds.write(session, data, now=datetime.now(UTC))
                foreign = await ds.foreign_in_categories(session, data)
            if foreign:
                logging.warning(
                    "В категориях набора %s чужих профилей: метки по построению неточны", foreign
                )
            try:
                found = await _runs(settings, engine, data, embedder or router, stages, explainer)
            finally:
                async with owner() as session, session.begin():
                    await ds.purge(session)
                    left = await ds.leftovers(session)
            if left:
                raise BenchError(f"после бенчмарка в базе осталось строк набора: {left}")
            per_repeat.append(summarize(found))
            needs += [{"repeat": repeat, **need} for need in found]
    finally:
        if router is not None:
            await router.aclose()
        await engine.dispose()
        await owner_engine.dispose()
    return {
        "variant": variant,
        "fake": isinstance(embedder, FakeEmbedder),
        "models": {
            "embedding": getattr(embedder, "model_name", None) or settings.llm.embedding.model,
            "rewriter": role_json(settings.llm.rewriter) if variant == "hyde" else None,
            "ranker": role_json(settings.llm.ranker) if use_ranker else None,
            "checker": role_json(settings.llm.checker) if explain else None,
        },
        "wall_seconds": round(time.monotonic() - started, 1),
        "usage": usage.as_json(),
        "settings": {
            "top_k": settings.vector.top_k,
            "exploration": settings.exploration.model_dump(),
            "ranker_batch_size": settings.llm_stage.batch_size,
            "ndcg_k": NDCG_K,
            "relevant_threshold": RELEVANT,
        },
        "dataset": {
            "needs": len(data.needs),
            "candidates": len(data.candidates),
            "relevance": {kind.value: label for kind, label in ds.RELEVANCE.items()},
            "cut": [kind.value for kind in ds.CUT],
            "foreign_in_categories": foreign,
        },
        "repeats": repeats,
        "summary": summarize(needs),
        "repeat_summaries": per_repeat,
        "needs": needs,
    }


async def _runs(
    settings: Settings,
    engine,
    data: ds.Dataset,
    embedder: Embedder | None,
    stages: RunStages,
    explainer: CountingExplainer | None,
) -> list[dict[str, Any]]:
    sessionmaker = make_sessionmaker(engine)
    service = RunService(
        sessionmaker,
        settings,
        embedder=embedder,
        summaries=CanonicalSummaries({c.id: c.summary for c in data.candidates}),
        stages=stages,
    )
    needs = []
    for need in data.needs:
        run_id = await service.create_run(data.need_id(need.key))
        if explainer is not None:
            explainer.reset()
        started = time.monotonic()
        await service.execute(run_id)
        seconds = time.monotonic() - started
        async with sessionmaker() as session:
            run = (await session.execute(_RUN, {"run": run_id})).mappings().one()
            rows = list((await session.execute(_RESULTS, {"run": run_id})).mappings())
        result = evaluate(data, need.key, run, rows)
        if explainer is not None:
            result |= {
                "explanations": explainer.explained,
                "explanations_checked": explainer.checked,
                "summary_written": explainer.summary_checked is not None,
                "summary_checked": bool(explainer.summary_checked),
            }
        needs.append(
            {"need": need.key, "origin": need.origin, "seconds": round(seconds, 1), **result}
        )
        logging.info(
            "%s: %s, nDCG@10 %s, recall@K %s, %.1f с",
            need.key,
            result["status"],
            result["ndcg_at_10"],
            result["recall_at_k"],
            seconds,
        )
    return needs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--variant", choices=VARIANTS, required=True)
    parser.add_argument("--k", type=int, required=True, help="vector.top_k прогона")
    parser.add_argument("--fake", action="store_true", help="без моделей: подделка эмбеддера")
    parser.add_argument(
        "--no-ranker", action="store_true", help="без ранжировщика: порядок по факторам"
    )
    parser.add_argument(
        "--no-explain", action="store_true", help="без объяснений выдачи и проверяльщика"
    )
    parser.add_argument("--repeat", type=int, default=1, help="сколько раз повторить прогоны")
    parser.add_argument(
        "--out", type=Path, help="файл результата; по умолчанию в tests/bench/results"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    owner_url = os.environ.get("MATCHER_TEST_OWNER_URL")
    if not owner_url:
        parser.error("не задан MATCHER_TEST_OWNER_URL: адрес базы api под владельцем таблиц")
    if args.fake and args.variant == "hyde":
        parser.error("--fake идёт без моделей: вариант hyde требует переписчика")
    use_ranker = not (args.fake or args.no_ranker)
    settings = bench_settings(Settings(), top_k=args.k)
    embedder = FakeEmbedder() if args.fake else None
    report = asyncio.run(
        run_bench(
            settings,
            owner_url=owner_url,
            variant=args.variant,
            use_ranker=use_ranker,
            explain=not args.no_explain,
            embedder=embedder,
            repeats=args.repeat,
        )
    )
    ranker = settings.llm.ranker
    name = f"{slug(ranker.model)}-r{ranker.reasoning_effort}" if use_ranker else NO_RANKER
    if use_ranker and args.no_explain:
        name += "-noexplain"
    embedding = "fake" if args.fake else slug(settings.llm.embedding.model)
    out = args.out or RESULTS / f"{args.variant}-k{args.k}-{name}-{embedding}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"результат: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
