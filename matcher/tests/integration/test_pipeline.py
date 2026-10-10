"""Префильтр, карточки, кэш векторов и прогон без LLM на демо-данных api (seed-demo)."""

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from matcher import embeddings, repository
from matcher.config import ExplorationSettings, Settings, VectorSettings
from matcher.db import MatchStage, RunStatus, make_engine, make_sessionmaker
from matcher.profile_text import Card, render_profile
from matcher.ranking import EXPLANATION_LIMIT
from matcher.runs import NeedNotFoundError, RunService
from tests.conftest import FakeEmbedder
from tests.integration.conftest import DEMO_NEED, purge_embeddings

NEED = uuid.UUID(DEMO_NEED)


def pool_id(n: int) -> uuid.UUID:
    """Кандидат POOL[n - 1] из api/app/db/seeds/demo.py."""
    return uuid.UUID(f"eeeeeeee-0000-4000-8000-{n:012d}")


ANNA = uuid.UUID("11111111-0000-4000-8000-000000000001")
PAVEL = uuid.UUID("11111111-0000-4000-8000-000000000002")
KIRILL, ALINA, TIMUR, SOFIA, GLEB, EDUARD, VERA, ROMAN, KSENIA = map(pool_id, range(1, 10))


async def test_prefilter_applies_hard_conditions_of_demo_need(
    matcher_session: AsyncSession,
) -> None:
    pool = set(await repository.prefilter(matcher_session, NEED))
    # Своя специализация, вилка пересекается, Москва или готовность к переезду. Глеб и
    # Эдуард с неподтверждённым грейдом остаются.
    assert {ANNA, KIRILL, ALINA, GLEB, EDUARD} <= pool
    # Вилка ниже или выше бюджета, офис в другом городе без переезда, чужая специализация.
    assert not {PAVEL, TIMUR, SOFIA, VERA, ROMAN, KSENIA} & pool


async def test_need_card_has_no_negative_prompt(matcher_session: AsyncSession) -> None:
    need = await repository.load_need(matcher_session, NEED)
    card = repository.need_card(need, await repository.need_facts(matcher_session, need))
    body = render_profile(card)
    assert need.negative_prompt and need.negative_prompt not in body
    assert need.positive_prompt in body and "Python" in body


async def test_candidate_card_respects_privacy_flags(owner_session: AsyncSession) -> None:
    shown = (await repository.candidates(owner_session, [KIRILL]))[KIRILL]
    assert shown.card.github and shown.card.fsp and shown.facts.fsp
    assert shown.card.negative_prompt and shown.card.negative_prompt not in render_profile(
        shown.card
    )
    await owner_session.execute(
        text(
            "UPDATE profile.candidate_privacy SET show_github = false, show_fsp = false "
            "WHERE candidate_id = :id"
        ),
        {"id": KIRILL},
    )
    hidden = (await repository.candidates(owner_session, [KIRILL]))[KIRILL]
    assert hidden.card.github == () and hidden.card.fsp == () and hidden.facts.fsp == ()
    body = render_profile(hidden.card)
    assert "pg-partitions" not in body and "ФСП" not in body


async def test_candidate_facts_tell_confirmed_from_unconfirmed_grade(
    matcher_session: AsyncSession,
) -> None:
    data = await repository.candidates(matcher_session, [KIRILL, GLEB])
    assert data[KIRILL].facts.grade_confirmed is True
    assert data[GLEB].facts.grade_confirmed is False
    assert data[GLEB].card.category_confirmed is False


async def test_embedding_cache_skips_unchanged_cards(matcher_session: AsyncSession) -> None:
    # Своя модель: векторы, которые прогоны других тестов зафиксировали в базе, не мешают.
    embedder = FakeEmbedder(model=f"fake-{uuid.uuid4().hex[:8]}")
    data = await repository.candidates(matcher_session, [KIRILL, ALINA])
    cards = {cid: item.card for cid, item in data.items()}
    assert await embeddings.refresh_candidates(matcher_session, embedder, cards, batch_size=1) == 2
    assert await embeddings.refresh_candidates(matcher_session, embedder, cards, batch_size=8) == 0
    changed = {KIRILL: cards[KIRILL], ALINA: Card(about="другой текст")}
    assert (
        await embeddings.refresh_candidates(matcher_session, embedder, changed, batch_size=8) == 1
    )
    stored = await matcher_session.scalar(
        text(
            "SELECT count(*) FROM match.candidate_embeddings "
            "WHERE candidate_id = ANY(:ids) AND model = :model"
        ),
        {"ids": [KIRILL, ALINA], "model": embedder.model_name},
    )
    assert stored == 2


async def test_dimension_check_rejects_wrong_model(matcher_session: AsyncSession) -> None:
    assert await embeddings.check_dimension(matcher_session, FakeEmbedder(1024)) == 1024
    with pytest.raises(embeddings.DimensionMismatchError, match="1024"):
        await embeddings.check_dimension(matcher_session, FakeEmbedder(3))


@pytest.fixture
async def service_factory(database_url: str) -> AsyncIterator:
    """RunService на роли matcher; прогоны и векторы фиксируются, поэтому тест их удаляет сам."""
    engine = make_engine(database_url)
    sessionmaker = make_sessionmaker(engine)
    created: list[uuid.UUID] = []

    async def make(top_k: int = 30, embedder=None) -> tuple[RunService, list[uuid.UUID]]:
        settings = Settings(
            database_url=database_url,
            vector=VectorSettings(top_k=top_k),
            exploration=ExplorationSettings(fresh=1, random=1),
        )
        return RunService(sessionmaker, settings, embedder=embedder), created

    yield make
    async with sessionmaker() as session, session.begin():
        await session.execute(
            text("DELETE FROM match.search_runs WHERE id = ANY(:ids)"), {"ids": created}
        )
        await purge_embeddings(session, FakeEmbedder().model_name)
    await engine.dispose()


async def _results(service_factory, run_id: uuid.UUID) -> tuple[dict, list[dict]]:
    service, _ = await service_factory()
    async with service._sessionmaker() as session:
        run = (
            (
                await session.execute(
                    text("SELECT * FROM match.search_runs WHERE id = :id"), {"id": run_id}
                )
            )
            .mappings()
            .one()
        )
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM match.search_results WHERE run_id = :id "
                        "ORDER BY rank NULLS LAST, candidate_id"
                    ),
                    {"id": run_id},
                )
            )
            .mappings()
            .all()
        )
    return dict(run), [dict(row) for row in rows]


async def test_small_pool_run_skips_vectors_and_needs_no_embedder(service_factory) -> None:
    service, created = await service_factory(top_k=30, embedder=None)
    run_id = await service.create_run(NEED)
    created.append(run_id)
    assert await service.execute(run_id) is RunStatus.DONE
    run, rows = await _results(service_factory, run_id)
    assert run["status"] == RunStatus.DONE and run["finished_at"] is not None
    assert run["params"]["vector_skipped"] is True
    assert run["pool_size"] == len(rows)
    assert all(row["stage"] == MatchStage.PREFILTER and row["rank"] for row in rows)


async def test_run_orders_confirmed_grade_first_with_factors(service_factory) -> None:
    service, created = await service_factory(top_k=2, embedder=FakeEmbedder())
    run_id = await service.create_run(NEED)
    created.append(run_id)
    assert await service.execute(run_id) is RunStatus.DONE
    run, rows = await _results(service_factory, run_id)
    assert run["params"]["vector_skipped"] is False
    assert run["params"]["embedding_model"] == "fake-embedder"
    ranked = [row for row in rows if row["rank"] is not None]
    rest = [row for row in rows if row["rank"] is None]
    assert len(ranked) == 2 + 2 and len(rest) == run["pool_size"] - len(ranked)
    assert all(row["stage"] == MatchStage.PREFILTER and row["vector_score"] for row in rest)
    confirmed = [row["factors"]["grade_confirmed"] for row in ranked]
    assert confirmed == sorted(confirmed, reverse=True)
    for row in ranked:
        assert row["stage"] == MatchStage.VECTOR
        assert row["explanation"] and len(row["explanation"]) <= EXPLANATION_LIMIT
        assert float(row["final_score"]) == pytest.approx(row["factors"]["factor_score"], abs=1e-4)
        assert set(row["factors"]["items"]) == {
            "stack",
            "grade",
            "salary",
            "percentile",
            "fsp",
            "freshness",
        }


async def test_large_pool_without_embedder_fails_with_reason(service_factory) -> None:
    service, created = await service_factory(top_k=2, embedder=None)
    run_id = await service.create_run(NEED)
    created.append(run_id)
    assert await service.execute(run_id) is RunStatus.FAILED
    run, rows = await _results(service_factory, run_id)
    assert run["status"] == RunStatus.FAILED and "эмбеддер" in run["error_message"]
    assert rows == []


async def test_unknown_need_is_rejected(service_factory) -> None:
    service, _ = await service_factory()
    with pytest.raises(NeedNotFoundError):
        await service.create_run(uuid.uuid4())
