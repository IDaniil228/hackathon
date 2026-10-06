import logging
import math
import random

import pytest
from sqlalchemy import select, update

from taskgen.bank.embeddings import refresh_embeddings
from taskgen.bank.importer import TemplateDocument, import_documents
from taskgen.bank.selection import Slot, jaccard_similarity
from taskgen.bank.selector import (
    BankShortageError,
    SelectionError,
    SelectionMode,
    select_templates,
)
from taskgen.config import SelectionSettings
from taskgen.db import Area, AreaSkill, Grade, Skill, TemplateEmbedding, TestConfig
from tests.bank.conftest import FakeEmbedder
from tests.bank.factory import make_essay, make_template

pytestmark = pytest.mark.integration

# Вектор навыков кандидата: FakeEmbedder отдаёт его для любого текста без явного вектора.
CANDIDATE = [1.0, 0.0, 0.0]
NEAR = [1.0, 0.0, 0.0]
MID = [1.0, 1.0, 0.0]
FAR = [0.0, 0.0, 1.0]
# Большинство тестов ниже про числовую часть отбора на шаблонах одного типа: другие типы
# в них не участвуют, а квота выключена там, где тест проверяет чистый softmax.
CODE_ONLY = {"essay_tasks": 0, "quiz_tasks": 0}
NO_QUOTA = CODE_ONLY | {"min_claimed_per_kind": 0}


def code_only(**fields) -> SelectionSettings:
    return SelectionSettings(**(CODE_ONLY | fields))


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


async def put(
    session, area, name, grade, *, vector=None, skills=("python",), kind="code", **fields
):
    make = make_essay if kind == "essay" else make_template
    text = make(
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
            settings=code_only(),
            embedder=embedder,
            overrides={"code_tasks": 3, "temperature": 0.0},
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
        settings=code_only(alpha=1.0, beta=2.0),
        embedder=embedder,
        overrides={"code_tasks": 3, "temperature": 0.0},
    )
    assert names(result) == [f"{area}-near", f"{area}-mid", f"{area}-far"]
    sims = [item.similarity for item in result.selected]
    assert sims == pytest.approx([1.0, 1 / math.sqrt(2), 0.0])
    for item in result.selected:
        assert item.distance == pytest.approx(1.0 * item.grade_gap + 2.0 * (1 - item.similarity))


async def test_result_carries_probabilities_and_slots_for_explanation(db_session, area):
    items = [
        await put(db_session, area, f"{area}-a", "middle", vector=NEAR),
        await put(db_session, area, f"{area}-a2", "middle", vector=FAR),
        await put(db_session, area, f"{area}-b", "senior", vector=FAR),
    ]
    embedder = await with_embeddings(db_session, items)
    result = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=["python"],
        rng=random.Random(1),
        settings=code_only(),
        embedder=embedder,
        overrides={"code_tasks": 2, "temperature": 0.5},
    )
    assert result.candidates == 3
    first, second = result.selected
    assert (first.slot, second.slot) == (Slot.QUOTA, Slot.EXPLORE)
    # Квотный слот выбирал только из двух шаблонов middle, разведочный — из всех трёх.
    own = {item.template.name: item.probability for item in result.ranking}
    assert first.template.grade_code == "middle"
    assert 0.0 < first.probability < 1.0
    assert first.probability > own[first.template.name]
    assert second.probability == pytest.approx(own[second.template.name])
    explanation = result.explanation()
    assert explanation["selection_mode"] == "embeddings"
    assert explanation["parameters"]["temperature"] == 0.5
    assert [row["name"] for row in explanation["selected"]] == names(result)
    assert [row["slot"] for row in explanation["selected"]] == ["quota", "explore"]
    assert [row["kind"] for row in explanation["selected"]] == ["code", "code"]


async def test_without_embedder_similarity_is_skill_overlap(db_session, area):
    await put(db_session, area, f"{area}-a", "middle", skills=("python", "sql"))
    await put(db_session, area, f"{area}-b", "middle", skills=("docker",))
    result = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=["python", "sql", "django"],
        rng=random.Random(0),
        settings=code_only(),
        embedder=None,
        overrides={"code_tasks": 2, "temperature": 0.0},
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
        settings=code_only(),
        embedder=embedder,
        overrides={"code_tasks": 2},
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
        settings=code_only(),
        embedder=embedder,
        overrides={"code_tasks": 1},
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
            settings=code_only(),
            embedder=embedder,
            overrides={"code_tasks": 1},
        )
    assert result.selection_mode is SelectionMode.SKILLS_JACCARD
    assert names(result) == [f"{area}-a"]
    assert "эмбеддер упал" in caplog.text


async def test_parameters_come_from_test_config_then_settings(db_session, area):
    await put(db_session, area, f"{area}-a", "middle")
    db_session.add(
        TestConfig(
            area_code=area,
            grade_code="middle",
            temperature=0.0,
            alpha=None,
            beta=3.0,
            code_tasks=1,
            min_claimed_per_kind=None,
        )
    )
    await db_session.flush()
    settings = code_only(temperature=0.9, alpha=2.0, beta=1.0, code_tasks=4)

    result = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=["python"],
        rng=random.Random(0),
        settings=settings,
    )
    assert result.parameters == code_only(temperature=0.0, alpha=2.0, beta=3.0, code_tasks=1)

    other_grade = await select_templates(
        db_session,
        area=area,
        grade="junior",
        skills=["python"],
        rng=random.Random(0),
        settings=settings,
        overrides={"min_claimed_per_kind": 0},
    )
    assert other_grade.parameters == code_only(
        temperature=0.9, alpha=2.0, beta=1.0, code_tasks=4, min_claimed_per_kind=0
    )

    explicit = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=["python"],
        rng=random.Random(0),
        settings=settings,
        overrides={"code_tasks": 2, "temperature": 0.5, "alpha": None},
    )
    assert explicit.parameters.temperature == 0.5
    assert explicit.parameters.code_tasks == 2
    assert explicit.parameters.beta == 3.0
    assert explicit.parameters.alpha == 2.0


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"tasks_per_test": 3}, "tasks_per_test"),
        ({"code_tasks": 0, "essay_tasks": 0, "quiz_tasks": 0}, "хотя бы одно задание"),
        ({"min_claimed_per_kind": -1}, "min_claimed_per_kind"),
    ],
)
async def test_invalid_parameters_are_rejected(db_session, area, overrides, message):
    await put(db_session, area, f"{area}-a", "middle")
    with pytest.raises(SelectionError, match=message):
        await select_templates(
            db_session,
            area=area,
            grade="middle",
            skills=["python"],
            rng=random.Random(0),
            settings=code_only(),
            overrides=overrides,
        )


async def test_k_comes_from_settings_and_explore_slots_are_capped_by_pool(db_session, area):
    for i in range(3):
        await put(db_session, area, f"{area}-{i}", "middle")
    result = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=["python"],
        rng=random.Random(0),
        settings=code_only(code_tasks=2),
    )
    assert len(result.selected) == 2
    capped = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=["python"],
        rng=random.Random(0),
        settings=code_only(code_tasks=10),
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
        settings=code_only(),
        overrides={"code_tasks": 5},
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
        settings=SelectionSettings(alpha=1.0, beta=0.0, **NO_QUOTA),
        overrides={"code_tasks": 2, "temperature": 0.0},
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
            settings=code_only(),
            **kwargs,
        )


async def test_ranking_keeps_every_candidate_by_probability_for_replacement(db_session, area):
    items = [
        await put(db_session, area, f"{area}-near", "middle", vector=NEAR),
        await put(db_session, area, f"{area}-mid", "middle", vector=MID),
        await put(db_session, area, f"{area}-far", "middle", vector=FAR),
        await put(db_session, area, f"{area}-j", "junior", vector=NEAR),
    ]
    embedder = await with_embeddings(db_session, items)
    result = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=["python"],
        rng=random.Random(1),
        settings=code_only(),
        embedder=embedder,
        overrides={"code_tasks": 1},
    )
    ranking = result.explanation()["ranking"]
    # Замена упавшего шаблона ищется среди всех кандидатов, а не только выбранных.
    assert len(ranking) == 4
    probabilities = [entry["probability"] for entry in ranking]
    assert probabilities == sorted(probabilities, reverse=True)
    middle = [entry["name"] for entry in ranking if entry["grade"] == "middle"]
    assert middle == [f"{area}-near", f"{area}-mid", f"{area}-far"]
    assert {entry["kind"] for entry in ranking} == {"code"}


async def test_candidate_text_combines_skills_profile_skills_and_resume(db_session, area):
    items = [await put(db_session, area, f"{area}-a", "middle", vector=NEAR)]
    embedder = await with_embeddings(db_session, items)
    titles = dict((await db_session.execute(select(Skill.code, Skill.title))).all())
    resume = "Пять лет пишу сервисы на Python и разбираю графы зависимостей."
    result = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=["python"],
        profile_skills=["pytorch", "python", "no-such-skill"],
        resume=resume,
        rng=random.Random(0),
        settings=code_only(),
        embedder=embedder,
    )
    assert result.selection_mode is SelectionMode.EMBEDDINGS
    [text] = embedder.calls[-1]
    assert titles["python"] in text and titles["pytorch"] in text
    # Код без названия в справочнике идёт в текст как есть, без повторов.
    assert "no-such-skill" in text
    assert text.replace(resume, "").count(titles["python"]) == 1
    assert resume in text


async def test_resume_alone_is_enough_for_embeddings(db_session, area):
    items = [await put(db_session, area, f"{area}-a", "middle", vector=NEAR)]
    embedder = await with_embeddings(db_session, items)
    result = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=[],
        resume="Резюме без списка навыков",
        rng=random.Random(0),
        settings=code_only(),
        embedder=embedder,
    )
    assert result.selection_mode is SelectionMode.EMBEDDINGS


async def test_empty_area_without_quota_gives_empty_selection(db_session, area):
    result = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=["python"],
        rng=random.Random(0),
        settings=SelectionSettings(**NO_QUOTA),
    )
    assert result.selected == [] and result.candidates == 0


# --- Состав по типам и квота заявленного грейда --------------------------------------------

GRADES = ("junior", "middle", "senior")
KINDS = ("code", "essay")


async def put_cell_bank(db_session, area, per_grade: int = 2) -> None:
    """По per_grade шаблонов каждого типа на каждый грейд, все с одинаковыми навыками."""
    for kind in KINDS:
        for grade in GRADES:
            for i in range(per_grade):
                await put(db_session, area, f"{area}-{kind}-{grade}-{i}", grade, kind=kind)


async def test_each_kind_is_selected_separately_in_kind_order(db_session, area):
    await put_cell_bank(db_session, area)
    result = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=["python"],
        rng=random.Random(3),
        settings=SelectionSettings(code_tasks=3, essay_tasks=2, quiz_tasks=0),
    )
    kinds = [item.template.kind for item in result.selected]
    assert kinds == ["code", "code", "code", "essay", "essay"]
    slots = [item.slot for item in result.selected]
    assert slots == [Slot.QUOTA, Slot.EXPLORE, Slot.EXPLORE, Slot.QUOTA, Slot.EXPLORE]


# Сколько сессий проверяет гарантия состава: сетка t × состав × seed.
COMPOSITION_TEMPERATURES = (0.0, 0.3, 0.7, 1.0)
COMPOSITIONS = (
    {"code_tasks": 1, "essay_tasks": 1},
    {"code_tasks": 3, "essay_tasks": 2},
    {"code_tasks": 1, "essay_tasks": 0},
    {"code_tasks": 4, "essay_tasks": 3},
)
COMPOSITION_SEEDS = 60


async def test_every_kind_has_a_task_of_claimed_grade_in_every_session(db_session, area):
    await put_cell_bank(db_session, area)
    checked = 0
    for t in COMPOSITION_TEMPERATURES:
        for counts in COMPOSITIONS:
            for seed in range(COMPOSITION_SEEDS):
                result = await select_templates(
                    db_session,
                    area=area,
                    grade="middle",
                    skills=["python"],
                    rng=random.Random(seed),
                    settings=SelectionSettings(quiz_tasks=0),
                    overrides=counts | {"temperature": t},
                )
                for kind in KINDS:
                    grades = [
                        item.template.grade_code
                        for item in result.selected
                        if item.template.kind == kind
                    ]
                    assert len(grades) == counts[f"{kind}_tasks"]
                    if grades:
                        assert "middle" in grades, (t, counts, seed, kind, grades)
                checked += 1
    assert checked == len(COMPOSITION_TEMPERATURES) * len(COMPOSITIONS) * COMPOSITION_SEEDS


async def test_missing_claimed_grade_of_a_kind_is_a_bank_error(db_session, area):
    # Эссе есть только у соседних грейдов: подменить middle на senior молча нельзя.
    await put(db_session, area, f"{area}-code", "middle")
    await put(db_session, area, f"{area}-essay-j", "junior", kind="essay")
    await put(db_session, area, f"{area}-essay-s", "senior", kind="essay")
    with pytest.raises(BankShortageError) as caught:
        await select_templates(
            db_session,
            area=area,
            grade="middle",
            skills=["python"],
            rng=random.Random(0),
            settings=SelectionSettings(code_tasks=1, essay_tasks=1, quiz_tasks=0),
        )
    message = str(caught.value)
    assert "essay" in message and "middle" in message and "min_claimed_per_kind" in message

    # Без квоты тот же банк собирает тест: ошибка — именно требование квоты.
    result = await select_templates(
        db_session,
        area=area,
        grade="middle",
        skills=["python"],
        rng=random.Random(0),
        settings=SelectionSettings(code_tasks=1, essay_tasks=1, quiz_tasks=0),
        overrides={"min_claimed_per_kind": 0},
    )
    assert [item.template.kind for item in result.selected] == ["code", "essay"]


async def test_quota_above_one_needs_that_many_claimed_templates(db_session, area):
    await put_cell_bank(db_session, area, per_grade=2)
    with pytest.raises(BankShortageError, match="требует 3"):
        await select_templates(
            db_session,
            area=area,
            grade="middle",
            skills=["python"],
            rng=random.Random(0),
            settings=SelectionSettings(code_tasks=4, essay_tasks=0, quiz_tasks=0),
            overrides={"min_claimed_per_kind": 3},
        )
