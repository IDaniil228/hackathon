import logging
import math
import random

import pytest
from sqlalchemy import select, update

from taskgen.bank.embeddings import refresh_embeddings
from taskgen.bank.importer import TemplateDocument, import_documents
from taskgen.bank.selection import jaccard_similarity
from taskgen.bank.selector import SelectionError, SelectionMode, select_templates
from taskgen.config import SelectionSettings
from taskgen.db import Area, AreaSkill, Grade, Skill, TemplateEmbedding, TestConfig
from tests.bank.conftest import FakeEmbedder
from tests.bank.factory import make_template

pytestmark = pytest.mark.integration

# Вектор навыков кандидата: FakeEmbedder отдаёт его для любого текста без явного вектора.
CANDIDATE = [1.0, 0.0, 0.0]
NEAR = [1.0, 0.0, 0.0]
MID = [1.0, 1.0, 0.0]
FAR = [0.0, 0.0, 1.0]


@pytest.fixture
async def area(db_session, unique) -> str:
    """Своя область в откатываемой транзакции: в отбор не попадут чужие шаблоны.

    С областью связаны все навыки справочника, иначе импорт отклонит шаблоны тестов.
    """
    db_session.add(Area(code=unique, title="тестовая область"))
    await db_session.flush()
    for code in await db_session.scalars(select(Skill.code)):
        db_session.add(AreaSkill(area_code=unique, skill_code=code))
    await db_session.flush()
    return unique


async def put(session, area, name, grade, *, vector=None, skills=("python",), **fields):
    text = make_template(
        name=name,
        area=area,
        grade=grade,
        skills=list(skills),
        description=f"{name} описание",
        **fields,
    )
    await import_documents(session, [TemplateDocument(f"{name}.md", text)])
    return f"{name} описание", vector


async def with_embeddings(session, items) -> FakeEmbedder:
    embedder = FakeEmbedder({text: vector for text, vector in items if vector is not None})
    await refresh_embeddings(session, embedder)
    return embedder


def names(result) -> list[str]:
    return [item.template.name for item in result.selected]


async def test_zero_temperature_takes_claimed_grade_then_nearest(db_session, area):
    items = [
        await put(db_session, area, f"{area}-j", "junior", vector=NEAR),
        await put(db_session, area, f"{area}-m1", "middle", vector=NEAR),
        await put(db_session, area, f"{area}-m2", "middle", vector=NEAR),
        await put(db_session, area, f"{area}-s", "senior", vector=NEAR),
    ]
    embedder = await with_embeddings(db_session, items)
    for seed in range(20):
        result = await select_templates(
            db_session,
            area=area,
            grade="middle",
            skills=["python"],
            rng=random.Random(seed),
            settings=SelectionSettings(),
            embedder=embedder,
            k=3,
            temperature=0.0,
        )
        assert result.selection_mode is SelectionMode.EMBEDDINGS
        assert sorted(names(result)[:2]) == [f"{area}-m1", f"{area}-m2"]
        assert names(result)[2] in {f"{area}-j", f"{area}-s"}
        assert [abs(item.grade_gap) for item in result.selected] == [0, 0, 1]


async def test_similarity_orders_templates_of_one_grade(db_session, area):
    items = [
        await put(db_session, area, f"{area}-far", "middle", vector=FAR),
        await put(db_session, area, f"{area}-near", "middle", vector=NEAR),
        await put(db_session, area, f"{area}-mid", "middle", vector=MID),
    ]
    embedder = await with_embeddings(db_session, items)
    result = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=["python"],
        rng=random.Random(0),
        settings=SelectionSettings(alpha=1.0, beta=2.0),
        embedder=embedder,
        k=3,
        temperature=0.0,
    )
    assert names(result) == [f"{area}-near", f"{area}-mid", f"{area}-far"]
    sims = [item.similarity for item in result.selected]
    assert sims == pytest.approx([1.0, 1 / math.sqrt(2), 0.0])
    for item in result.selected:
        assert item.distance == pytest.approx(1.0 * item.grade_gap + 2.0 * (1 - item.similarity))


async def test_result_carries_probabilities_for_explanation(db_session, area):
    items = [
        await put(db_session, area, f"{area}-a", "middle", vector=NEAR),
        await put(db_session, area, f"{area}-b", "senior", vector=FAR),
    ]
    embedder = await with_embeddings(db_session, items)
    result = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=["python"],
        rng=random.Random(1),
        settings=SelectionSettings(),
        embedder=embedder,
        k=2,
        temperature=0.5,
    )
    assert result.candidates == 2
    first, second = result.selected
    assert 0.0 < first.probability < 1.0
    assert first.probability + second.probability == pytest.approx(1.0)
    assert second.step_probability == pytest.approx(1.0)
    explanation = result.explanation()
    assert explanation["selection_mode"] == "embeddings"
    assert explanation["parameters"]["temperature"] == 0.5
    assert [row["name"] for row in explanation["selected"]] == names(result)


async def test_without_embedder_similarity_is_skill_overlap(db_session, area):
    await put(db_session, area, f"{area}-a", "middle", skills=("python", "sql"))
    await put(db_session, area, f"{area}-b", "middle", skills=("docker",))
    result = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=["python", "sql", "django"],
        rng=random.Random(0),
        settings=SelectionSettings(),
        embedder=None,
        k=2,
        temperature=0.0,
    )
    assert result.selection_mode is SelectionMode.SKILLS_JACCARD
    assert result.fallback_reason
    by_name = {item.template.name: item for item in result.selected}
    assert by_name[f"{area}-a"].similarity == pytest.approx(
        jaccard_similarity({"python", "sql"}, {"python", "sql", "django"})
    )
    assert by_name[f"{area}-b"].similarity == 0.0
    assert names(result)[0] == f"{area}-a"


async def test_missing_vector_for_any_candidate_falls_back(db_session, area):
    items = [await put(db_session, area, f"{area}-a", "middle", vector=NEAR)]
    embedder = await with_embeddings(db_session, items)
    await put(db_session, area, f"{area}-b", "middle")
    result = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=["python"],
        rng=random.Random(0),
        settings=SelectionSettings(),
        embedder=embedder,
        k=2,
    )
    assert result.selection_mode is SelectionMode.SKILLS_JACCARD
    assert "1" in result.fallback_reason


async def test_stale_vector_is_not_used(db_session, area):
    items = [await put(db_session, area, f"{area}-a", "middle", vector=NEAR)]
    embedder = await with_embeddings(db_session, items)
    await db_session.execute(
        update(TemplateEmbedding)
        .where(TemplateEmbedding.model == embedder.model_name)
        .values(content_hash="0" * 64)
    )
    result = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=["python"],
        rng=random.Random(0),
        settings=SelectionSettings(),
        embedder=embedder,
        k=1,
    )
    assert result.selection_mode is SelectionMode.SKILLS_JACCARD


async def test_embedder_error_falls_back_and_is_logged(db_session, area, caplog):
    items = [await put(db_session, area, f"{area}-a", "middle", vector=NEAR)]
    embedder = await with_embeddings(db_session, items)
    embedder.fail = RuntimeError("эмбеддер упал")
    with caplog.at_level(logging.WARNING, logger="taskgen.bank.selector"):
        result = await select_templates(
            db_session,
            area=area,
            grade="middle",
            skills=["python"],
            rng=random.Random(0),
            settings=SelectionSettings(),
            embedder=embedder,
            k=1,
        )
    assert result.selection_mode is SelectionMode.SKILLS_JACCARD
    assert names(result) == [f"{area}-a"]
    assert "эмбеддер упал" in caplog.text


async def test_parameters_come_from_test_config_then_settings(db_session, area):
    await put(db_session, area, f"{area}-a", "middle")
    db_session.add(
        TestConfig(area_code=area, grade_code="middle", temperature=0.0, alpha=None, beta=3.0)
    )
    await db_session.flush()
    settings = SelectionSettings(temperature=0.9, alpha=2.0, beta=1.0, tasks_per_test=4)

    result = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=["python"],
        rng=random.Random(0),
        settings=settings,
    )
    assert result.parameters == SelectionSettings(
        temperature=0.0, alpha=2.0, beta=3.0, tasks_per_test=4
    )

    other_grade = await select_templates(
        db_session,
        area=area,
        grade="junior",
        skills=["python"],
        rng=random.Random(0),
        settings=settings,
    )
    assert other_grade.parameters == settings

    explicit = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=["python"],
        rng=random.Random(0),
        settings=settings,
        k=1,
        temperature=0.5,
    )
    assert explicit.parameters.temperature == 0.5
    assert explicit.parameters.tasks_per_test == 1
    assert explicit.parameters.beta == 3.0


async def test_k_defaults_to_tasks_per_test_and_is_capped_by_pool(db_session, area):
    for i in range(3):
        await put(db_session, area, f"{area}-{i}", "middle")
    result = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=["python"],
        rng=random.Random(0),
        settings=SelectionSettings(tasks_per_test=2),
    )
    assert len(result.selected) == 2
    capped = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=["python"],
        rng=random.Random(0),
        settings=SelectionSettings(tasks_per_test=10),
    )
    assert len(capped.selected) == 3
    assert len(set(names(capped))) == 3


async def test_only_current_versions_are_candidates(db_session, area):
    await put(db_session, area, f"{area}-a", "middle")
    await put(db_session, area, f"{area}-a", "middle", difficulty=7)
    result = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=["python"],
        rng=random.Random(0),
        settings=SelectionSettings(),
        k=5,
    )
    assert [item.template.version for item in result.selected] == [2]


async def test_intermediate_grade_counts_as_one_step(db_session, area):
    # junior+ с level 15 стоит между junior и middle; разности level до соседей 5 и 10.
    db_session.add(Grade(code="junior+", level=15, title="Junior+"))
    await db_session.flush()
    await put(db_session, area, f"{area}-jp", "junior+")
    await put(db_session, area, f"{area}-s", "senior")
    result = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=["python"],
        rng=random.Random(0),
        settings=SelectionSettings(alpha=1.0, beta=0.0),
        k=2,
        temperature=0.0,
    )
    gaps = {item.template.grade_code: item.grade_gap for item in result.selected}
    assert gaps == {"junior+": -1, "senior": 1}
    assert result.selected[0].distance == result.selected[1].distance
    # Две равные ничьи: на первом шаге у каждого вероятность ровно половина.
    assert result.selected[0].step_probability == pytest.approx(0.5)


@pytest.mark.parametrize(("field", "value"), [("area", "nope-area"), ("grade", "guru")])
async def test_unknown_area_or_grade_is_an_error(db_session, area, field, value):
    kwargs = {"area": area, "grade": "middle", field: value}
    with pytest.raises(SelectionError):
        await select_templates(
            db_session,
            skills=["python"],
            rng=random.Random(0),
            settings=SelectionSettings(),
            **kwargs,
        )


async def test_empty_area_gives_empty_selection(db_session, area):
    result = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=["python"],
        rng=random.Random(0),
        settings=SelectionSettings(),
    )
    assert result.selected == [] and result.candidates == 0
