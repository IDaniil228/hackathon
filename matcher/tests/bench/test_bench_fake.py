"""Бенчмарк без моделей на своей базе api: механика набора, прогонов и уборки.

Нужны те же переменные, что интеграционным тестам (tests/integration/conftest.py). Метрики
порядка здесь не проверяются: с подделкой эмбеддера и без LLM они ничего не говорят о
качестве. Проверяется то, что следует из построения при любом эмбеддере и при чужих
профилях в базе: база интеграционных тестов содержит демо-данные, и их кандидаты меняют
процентиль силы профиля в общих категориях.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from matcher.config import Settings
from tests.bench import dataset as ds
from tests.conftest import FakeEmbedder
from tests.integration.conftest import _required

pytestmark = pytest.mark.integration

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "match_bench.py"
_spec = importlib.util.spec_from_file_location("match_bench", SCRIPT)
bench = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bench
_spec.loader.exec_module(bench)


async def fake_report(top_k: int) -> dict:
    base = Settings(_env_file=None, database_url=_required("MATCHER_DATABASE_URL"))
    settings = bench.bench_settings(base, top_k=top_k)
    return await bench.run_bench(
        settings,
        owner_url=_required("MATCHER_TEST_OWNER_URL"),
        variant="structured",
        use_ranker=False,
        embedder=FakeEmbedder(),
    )


def kind_rank(need: dict, kind: ds.Kind) -> int:
    return next(r["rank"] for r in need["ranking"] if r["kind"] == kind.value)


async def test_small_k_runs_vector_stage_and_prefilter_cuts_by_construction() -> None:
    report = await fake_report(top_k=3)
    assert report["fake"] is True
    for need in report["needs"]:
        assert need["status"] == "done", need["error"]
        assert need["prefilter_ok"], need["need"]
        assert need["vector_skipped"] is False
        assert need["recall_at_k"] is not None


async def test_large_k_skips_vector_stage_and_orders_by_factors() -> None:
    report = await fake_report(top_k=1000)
    assert {need["vector_skipped"] for need in report["needs"]} == {True}
    assert {need["recall_at_k"] for need in report["needs"]} == {None}
    assert report["summary"]["prefilter_ok"] == f"{len(ds.NEEDS)}/{len(ds.NEEDS)}"
    for need in report["needs"]:
        # Без LLM порядок задают факторы: заявленные навыки накрутчика дают ему меньший стек.
        assert need["stuffer_below_ideal"], need["need"]
        # Неподтверждённый грейд ниже любого подтверждённого.
        assert kind_rank(need, ds.Kind.IDEAL_IMPORT) == max(
            r["rank"] for r in need["ranking"] if r["kind"] != "foreign"
        )


async def test_bench_leaves_no_rows_behind() -> None:
    await fake_report(top_k=3)
    engine = bench.make_engine(_required("MATCHER_TEST_OWNER_URL"))
    async with bench.make_sessionmaker(engine)() as session:
        assert await ds.leftovers(session) == 0
    await engine.dispose()
