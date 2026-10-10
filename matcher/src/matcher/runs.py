"""Прогон подбора по потребности: префильтр → вектор → LLM → выдача в match.search_*.

Без LLM-стадии порядок — подтверждённый грейд, factor_score, vector_score, а объяснение и
обзор прогона — шаблоны из строк факторов. LLM-стадию подключают швы RunStages
(matcher.stages): rewriter строит карточку запроса (HyDE) вместо карточки из структурных
полей, ranker даёт вердикт кандидатам короткого списка, explainer проверяет объяснения и
обзор. Стадия, которой модель не ответила, выпадает, а прогон завершается без неё.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from matcher import embeddings, repository
from matcher.config import Settings
from matcher.db import MatchStage, RunStatus
from matcher.explain import explanation_facts, run_facts, template_summary
from matcher.factors import compute_factors, factor_score
from matcher.llm.contracts import Embedder
from matcher.profile_text import Card, render_profile
from matcher.ranking import Ranked, needs_vectors, order, shortlist, template_explanation
from matcher.stages import LLMUnavailableError, RankInput, RunStages
from matcher.summaries import SummarySource, summaries_for

logger = logging.getLogger(__name__)

# Пределы колонок схемы api: match.search_runs.error_message varchar(500),
# match.search_runs.llm_model varchar(60), match.chat_threads.title varchar(120).
ERROR_LIMIT = 500
MODEL_LIMIT = 60
TITLE_LIMIT = 120
# Сколько карточек пишется одной транзакцией при пересчёте всех векторов: прерванный
# пересчёт сохраняет сделанное.
EMBED_CHUNK = 200


class RunError(Exception):
    """Прогон не может завершиться; текст уходит в search_runs.error_message."""


class NeedNotFoundError(LookupError):
    pass


class NeedInactiveError(RuntimeError):
    pass


class RunNotFoundError(LookupError):
    pass


class RunNotFinishedError(RuntimeError):
    """Уточнять можно только прогон со статусом done."""


class ChatAuthorMissingError(ValueError):
    """У потребности нет треда чата, а создать его не от кого: автор не известен."""


@dataclass
class _Outcome:
    pool_size: int
    params: dict[str, Any]
    ranked: list[Ranked]
    summary: str
    llm_model: str | None = None
    # Кандидаты пула вне короткого списка → их vector_score или None.
    rest: dict[uuid.UUID, float | None] = field(default_factory=dict)
    stage: MatchStage = MatchStage.VECTOR


async def candidate_cards(
    session: AsyncSession,
    ids: list[uuid.UUID],
    summaries: SummarySource | None,
    *,
    concurrency: int,
) -> dict[uuid.UUID, Card]:
    """Карточки кандидатов из БД вместе с machine summary из taskgen."""
    data = await repository.candidates(session, ids)
    texts = await summaries_for(summaries, ids, concurrency=concurrency)
    return {cid: replace(item.card, summary=texts.get(cid)) for cid, item in data.items()}


def _round(value: float | None, low: float) -> float | None:
    # numeric(6, 5) в схеме api: пять знаков, и значение внутри CHECK-диапазона колонки.
    return None if value is None else round(min(1.0, max(low, value)), 5)


def _result_factors(item: Ranked) -> dict[str, Any]:
    facts = explanation_facts(item)
    if item.verdict is not None:
        facts["llm"] = {
            "need_negative_hits": item.verdict.need_negative_hits,
            "candidate_negative_hits": item.verdict.candidate_negative_hits,
        }
    return facts


_INSERT_RUN = text(
    """
    INSERT INTO match.search_runs
        (need_id, author_user_id, parent_run_id, strategy, status, params)
    VALUES (:need_id, :author, :parent, :strategy, CAST(:status AS match.run_status),
            CAST(:params AS jsonb))
    RETURNING id
    """
)


class RunService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        settings: Settings,
        *,
        embedder: Embedder | None,
        summaries: SummarySource | None = None,
        stages: RunStages | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._embedder = embedder
        self._summaries = summaries
        self._stages = stages or RunStages()
        self._clock = clock

    @property
    def embedder(self) -> Embedder | None:
        return self._embedder

    async def create_run(
        self, need_id: uuid.UUID, *, author_user_id: uuid.UUID | None = None
    ) -> uuid.UUID:
        """Строка прогона со статусом queued; исполняет её execute."""
        async with self._sessionmaker() as session, session.begin():
            need = await repository.load_need(session, need_id)
            if need is None:
                raise NeedNotFoundError(f"потребности {need_id} нет")
            if not need.is_active:
                raise NeedInactiveError(f"потребность {need_id} закрыта")
            return await self._insert_run(session, need_id, author_user_id, None, [])

    async def _insert_run(
        self,
        session: AsyncSession,
        need_id: uuid.UUID,
        author: uuid.UUID | None,
        parent: uuid.UUID | None,
        refinements: list[str],
    ) -> uuid.UUID:
        # Уточнения — вход прогона: execute читает их из params и дополняет params итогом.
        params = {"refinements": refinements} if refinements else None
        return await session.scalar(
            _INSERT_RUN,
            {
                "need_id": need_id,
                "author": author,
                "parent": parent,
                "strategy": self._strategy(),
                "status": RunStatus.QUEUED.value,
                "params": json.dumps(params, ensure_ascii=False) if params else None,
            },
        )

    async def refine(
        self, run_id: uuid.UUID, message: str, *, author_user_id: uuid.UUID | None = None
    ) -> uuid.UUID:
        """Новый прогон с parent_run_id и уточнениями цепочки, плюс две реплики чата.

        Реплика работодателя пишется без run_id, ответ сервиса — с id нового прогона, в тред
        потребности; без треда он создаётся от author_user_id или автора потребности.
        Исполняет новый прогон execute.
        """
        async with self._sessionmaker() as session, session.begin():
            parent = (
                (
                    await session.execute(
                        text(
                            "SELECT need_id, status::text AS status, params "
                            "FROM match.search_runs WHERE id = :id"
                        ),
                        {"id": run_id},
                    )
                )
                .mappings()
                .first()
            )
            if parent is None:
                raise RunNotFoundError(f"прогона {run_id} нет")
            if parent["status"] != RunStatus.DONE:
                raise RunNotFinishedError(f"прогон {run_id} в статусе {parent['status']}")
            need = await repository.load_need(session, parent["need_id"])
            if need is None or not need.is_active:
                raise NeedInactiveError(f"потребность {parent['need_id']} закрыта или удалена")
            chain = [*(parent["params"] or {}).get("refinements", []), message]
            author = author_user_id or need.author_user_id
            new_id = await self._insert_run(session, need.id, author, run_id, chain)
            thread = await session.scalar(
                text(
                    "SELECT id FROM match.chat_threads WHERE need_id = :need "
                    "ORDER BY updated_at DESC, id DESC LIMIT 1"
                ),
                {"need": need.id},
            )
            if thread is None:
                if author is None:
                    raise ChatAuthorMissingError(
                        f"у потребности {need.id} нет треда чата и автора: передайте author_user_id"
                    )
                thread = await session.scalar(
                    text(
                        "INSERT INTO match.chat_threads (need_id, user_id, title) "
                        "VALUES (:need, :user, :title) RETURNING id"
                    ),
                    {"need": need.id, "user": author, "title": need.title[:TITLE_LIMIT]},
                )
            await session.execute(
                text(
                    """
                    INSERT INTO match.chat_messages (thread_id, run_id, role, content,
                                                     tool_payload)
                    VALUES (:thread, NULL, 'user', :message, NULL),
                           (:thread, :run, 'assistant', :reply, CAST(:payload AS jsonb))
                    """
                ),
                {
                    "thread": thread,
                    "run": new_id,
                    "message": message,
                    "reply": "Запущен уточнённый подбор с учётом сообщения",
                    "payload": json.dumps({"run_id": str(new_id), "parent_run_id": str(run_id)}),
                },
            )
            await session.execute(
                text("UPDATE match.chat_threads SET updated_at = now() WHERE id = :id"),
                {"id": thread},
            )
            return new_id

    async def pending_runs(self) -> list[uuid.UUID]:
        """Прогоны, которые остались в queued или running, например после перезапуска.

        Прогон пишет выдачу одной транзакцией, поэтому у прерванного прогона выдачи нет, и
        его можно исполнить заново.
        """
        async with self._sessionmaker() as session:
            rows = await session.scalars(
                text(
                    "SELECT id FROM match.search_runs "
                    "WHERE status IN ('queued', 'running') ORDER BY created_at, id"
                )
            )
            return list(rows)

    def _strategy(self) -> str:
        return "prefilter_vector_llm" if self._stages.uses_llm else "prefilter_vector"

    async def execute(self, run_id: uuid.UUID) -> RunStatus:
        """Исполняет прогон и пишет итог; ошибку любого вида пишет в error_message."""
        await self._set_status(run_id, RunStatus.RUNNING)
        try:
            async with self._sessionmaker() as session, session.begin():
                run = (
                    (
                        await session.execute(
                            text("SELECT need_id, params FROM match.search_runs WHERE id = :id"),
                            {"id": run_id},
                        )
                    )
                    .mappings()
                    .one()
                )
                refinements = (run["params"] or {}).get("refinements", [])
                outcome = await self._pipeline(session, run_id, run["need_id"], refinements)
                await self._store(session, run_id, outcome)
        except Exception as error:
            logger.exception("Прогон %s не удался", run_id)
            message = str(error) if isinstance(error, RunError) else f"внутренняя ошибка: {error}"
            await self._set_status(run_id, RunStatus.FAILED, error=message)
            return RunStatus.FAILED
        return RunStatus.DONE

    async def _set_status(
        self, run_id: uuid.UUID, status: RunStatus, *, error: str | None = None
    ) -> None:
        finished = status in (RunStatus.DONE, RunStatus.FAILED)
        async with self._sessionmaker() as session, session.begin():
            await session.execute(
                text(
                    """
                    UPDATE match.search_runs
                    SET status = CAST(:status AS match.run_status), error_message = :error,
                        finished_at = CASE WHEN :finished THEN now() END
                    WHERE id = :id
                    """
                ),
                {
                    "id": run_id,
                    "status": status.value,
                    "error": error[:ERROR_LIMIT] if error else None,
                    "finished": finished,
                },
            )

    async def _pipeline(
        self,
        session: AsyncSession,
        run_id: uuid.UUID,
        need_id: uuid.UUID,
        refinements: list[str],
    ) -> _Outcome:
        need = await repository.load_need(session, need_id)
        if need is None:
            raise RunError(f"потребности {need_id} больше нет")
        facts = await repository.need_facts(session, need)
        structured = repository.need_card(need, facts)
        pool = await repository.prefilter(session, need_id)
        top_k = self._settings.vector.top_k
        stages = self._stages
        params: dict[str, Any] = {
            "top_k": top_k,
            "query_variant": "structured",
            "llm_stage": stages.uses_llm,
            "vector_skipped": not needs_vectors(len(pool), top_k=top_k),
        }
        if refinements:
            params["refinements"] = refinements

        cards: dict[uuid.UUID, Card] = {}
        query = structured
        scores: dict[uuid.UUID, float] | None = None
        if needs_vectors(len(pool), top_k=top_k) or stages.ranker is not None:
            cards = await self._cards(session, list(pool))
        if stages.rewriter is not None:
            try:
                query = await stages.rewriter.rewrite(need, structured, refinements)
                params["query_variant"] = "hyde"
            except LLMUnavailableError as error:
                logger.warning("Прогон %s: карточка запроса из полей, %s", run_id, error)
                params["rewriter_error"] = str(error)[:ERROR_LIMIT]
        params["query_profile"] = render_profile(query)
        if needs_vectors(len(pool), top_k=top_k):
            if self._embedder is None:
                raise RunError(
                    f"пул {len(pool)} больше top_k {top_k}, а эмбеддер не настроен: "
                    "без векторной стадии короткий список не собрать"
                )
            await embeddings.refresh_candidates(
                session, self._embedder, cards, batch_size=self._settings.embeddings.batch_size
            )
            vector, digest = await embeddings.need_vector(session, self._embedder, need_id, query)
            scores = await embeddings.vector_scores(
                session, self._embedder.model_name, vector, list(pool)
            )
            params |= {"embedding_model": self._embedder.model_name, "query_hash": digest}

        picked = shortlist(
            pool,
            scores,
            top_k=top_k,
            run_id=run_id,
            fresh=self._settings.exploration.fresh,
            random_count=self._settings.exploration.random,
        )
        params["exploration"] = {
            "seed": str(run_id),
            "fresh": [str(cid) for cid in picked.exploration.fresh],
            "random": [str(cid) for cid in picked.exploration.random],
        }

        data = await repository.candidates(session, picked.ranked)
        now = self._clock()
        ranked = []
        for cid in picked.ranked:
            factors = compute_factors(facts, data[cid].facts, now=now)
            ranked.append(
                Ranked(
                    candidate_id=cid,
                    grade_confirmed=data[cid].facts.grade_confirmed,
                    factor_score=factor_score(factors),
                    vector_score=scores.get(cid) if scores is not None else None,
                    factors=factors,
                )
            )

        llm_model = None
        if stages.ranker is not None:
            items = [RankInput(r.candidate_id, cards[r.candidate_id], r.factors) for r in ranked]
            try:
                verdicts = await stages.ranker.score(need, query, items, refinements)
            except LLMUnavailableError as error:
                logger.warning("Прогон %s: порядок по факторам, %s", run_id, error)
                params |= {"llm_fallback": True, "llm_error": str(error)[:ERROR_LIMIT]}
            else:
                params["llm_fallback"] = False
                llm_model = stages.ranker.model[:MODEL_LIMIT]
                for item in ranked:
                    item.verdict = verdicts.get(item.candidate_id)
        ranked = order(ranked)

        explainer = stages.explainer
        if explainer is not None and llm_model is not None:
            texts = await asyncio.gather(*(explainer.explain(item) for item in ranked))
        else:
            texts = [template_explanation(item.grade_confirmed, item.factors) for item in ranked]
        for item, explanation in zip(ranked, texts, strict=True):
            item.explanation = explanation

        overview = run_facts(ranked, pool_size=len(pool), vector_skipped=picked.vector_skipped)
        if explainer is not None and llm_model is not None:
            summary = await explainer.summarize(overview)
        else:
            summary = template_summary(overview)

        rest = {cid: scores.get(cid) for cid in picked.rest} if scores is not None else {}
        stage = MatchStage.PREFILTER if picked.vector_skipped else MatchStage.VECTOR
        return _Outcome(
            pool_size=len(pool),
            params=params,
            ranked=ranked,
            summary=summary,
            llm_model=llm_model,
            rest=rest,
            stage=stage,
        )

    async def _cards(self, session: AsyncSession, ids: list[uuid.UUID]) -> dict[uuid.UUID, Card]:
        return await candidate_cards(
            session, ids, self._summaries, concurrency=self._settings.taskgen.concurrency
        )

    async def refresh_embeddings(self) -> tuple[int, int]:
        """Досчитывает устаревшие векторы всех кандидатов, которых можно найти.

        Возвращает число кандидатов и число записанных векторов.
        """
        if self._embedder is None:
            raise RunError("эмбеддер не настроен")
        async with self._sessionmaker() as session:
            ids = await repository.searchable_candidates(session)
        written = 0
        for start in range(0, len(ids), EMBED_CHUNK):
            chunk = ids[start : start + EMBED_CHUNK]
            async with self._sessionmaker() as session, session.begin():
                cards = await self._cards(session, chunk)
                written += await embeddings.refresh_candidates(
                    session, self._embedder, cards, batch_size=self._settings.embeddings.batch_size
                )
        return len(ids), written

    async def _store(self, session: AsyncSession, run_id: uuid.UUID, outcome: _Outcome) -> None:
        rows = [
            {
                "run_id": run_id,
                "candidate_id": item.candidate_id,
                "stage": (MatchStage.LLM if item.llm_score is not None else outcome.stage).value,
                "rank": rank,
                "vector_score": _round(item.vector_score, -1.0),
                "llm_score": _round(item.llm_score, 0.0),
                "final_score": _round(
                    item.llm_score if item.llm_score is not None else item.factor_score, 0.0
                ),
                "explanation": item.explanation,
                "factors": json.dumps(_result_factors(item), ensure_ascii=False),
            }
            for rank, item in enumerate(outcome.ranked, start=1)
        ]
        if self._settings.results.store_pool:
            rows += [
                {
                    "run_id": run_id,
                    "candidate_id": cid,
                    "stage": MatchStage.PREFILTER.value,
                    "rank": None,
                    "vector_score": _round(score, -1.0),
                    "llm_score": None,
                    "final_score": None,
                    "explanation": None,
                    "factors": None,
                }
                for cid, score in outcome.rest.items()
            ]
        if rows:
            await session.execute(
                text(
                    """
                    INSERT INTO match.search_results
                        (run_id, candidate_id, stage, rank, vector_score, llm_score,
                         final_score, explanation, factors)
                    VALUES (:run_id, :candidate_id, CAST(:stage AS match.match_stage), :rank,
                            :vector_score, :llm_score, :final_score, :explanation,
                            CAST(:factors AS jsonb))
                    """
                ),
                rows,
            )
        await session.execute(
            text(
                """
                UPDATE match.search_runs
                SET status = CAST(:status AS match.run_status), pool_size = :pool_size,
                    params = CAST(:params AS jsonb), summary = :summary, llm_model = :llm_model,
                    finished_at = now(), error_message = NULL
                WHERE id = :id
                """
            ),
            {
                "id": run_id,
                "status": RunStatus.DONE.value,
                "pool_size": outcome.pool_size,
                "params": json.dumps(outcome.params, ensure_ascii=False),
                "summary": outcome.summary,
                "llm_model": outcome.llm_model,
            },
        )
