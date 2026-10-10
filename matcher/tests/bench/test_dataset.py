"""Набор бенчмарка: каждый вид кандидата отличается от идеального ровно тем, что задаёт метку."""

from collections import Counter

import pytest

from tests.bench.dataset import CUT, INJECTION, NEEDS, PREFIX, RELEVANCE, Kind, build

DATASET = build()


def cand(need_key: str, kind: Kind):
    return next(c for c in DATASET.of(need_key) if c.kind == kind)


def test_every_need_has_every_kind_once() -> None:
    for need in NEEDS:
        assert Counter(c.kind for c in DATASET.of(need.key)) == Counter(Kind)


def test_labels_cover_kinds_the_prefilter_keeps() -> None:
    assert set(RELEVANCE) | set(CUT) == set(Kind)
    assert not set(RELEVANCE) & set(CUT)
    for need in NEEDS:
        assert sorted(DATASET.labels(need.key).values()) == sorted(RELEVANCE.values())


def test_ids_are_unique_and_carry_the_prefix() -> None:
    ids = [c.id for c in DATASET.candidates] + [DATASET.need_id(n.key) for n in NEEDS]
    assert len(set(ids)) == len(ids)
    assert all(str(i).startswith(f"{PREFIX}-") for i in ids)


def test_ties_by_id_go_against_the_more_relevant() -> None:
    # Матчер разбивает ничью по возрастанию id.
    for need in NEEDS:
        ideal = cand(need.key, Kind.IDEAL).id
        for kind in (Kind.VIOLATOR, Kind.STUFFER, Kind.GRADE_LOWER, Kind.NO_REQUIRED):
            assert cand(need.key, kind).id < ideal


def test_candidate_has_each_skill_once() -> None:
    for c in DATASET.candidates:
        codes = [skill.code for skill in c.skills]
        assert len(codes) == len(set(codes)), (c.need_key, c.kind)


@pytest.mark.parametrize("need", NEEDS, ids=lambda n: n.key)
def test_ideal_meets_every_skill_inside_budget(need) -> None:
    ideal = cand(need.key, Kind.IDEAL)
    levels = {s.code: s.confirmed_level for s in ideal.skills}
    for code, minimum in (*need.required, *need.nice):
        assert levels[code] is not None and levels[code] >= minimum
    assert need.salary[0] <= ideal.salary[0] <= ideal.salary[1] <= need.salary[1]
    assert ideal.grade_source == "test" and ideal.category == need.category


@pytest.mark.parametrize("need", NEEDS, ids=lambda n: n.key)
def test_perturbations_change_only_their_own_field(need) -> None:
    ideal = cand(need.key, Kind.IDEAL)
    nice = {code for code, _ in need.nice}

    no_nice = cand(need.key, Kind.NO_NICE)
    assert {s.code for s in no_nice.skills} == {s.code for s in ideal.skills} - nice

    no_required = cand(need.key, Kind.NO_REQUIRED)
    assert {s.code for s in no_required.skills} == {s.code for s in ideal.skills} - {
        need.drop_required
    }
    assert need.drop_required in {code for code, _ in need.required}

    lower = cand(need.key, Kind.GRADE_LOWER)
    assert lower.category != ideal.category
    assert lower.category.split(".")[0] == need.specialization
    assert lower.skills == ideal.skills

    imported = cand(need.key, Kind.IDEAL_IMPORT)
    assert imported.grade_source == "import" and imported.summary is None
    assert [s.code for s in imported.skills] == [s.code for s in ideal.skills]
    assert all(s.confirmed_level is None for s in imported.skills)

    violator = cand(need.key, Kind.VIOLATOR)
    assert (violator.skills, violator.category, violator.salary, violator.summary) == (
        ideal.skills,
        ideal.category,
        ideal.salary,
        ideal.summary,
    )
    assert violator.about != ideal.about


@pytest.mark.parametrize("need", NEEDS, ids=lambda n: n.key)
def test_common_about_names_no_skill_some_kind_lacks(need) -> None:
    for code, _ in (*need.nice, (need.drop_required, 0)):
        assert code.lower() not in need.about.lower()


@pytest.mark.parametrize("need", NEEDS, ids=lambda n: n.key)
def test_stuffer_claims_everything_and_confirms_nothing(need) -> None:
    stuffer = cand(need.key, Kind.STUFFER)
    codes = {s.code for s in stuffer.skills}
    assert {code for code, _ in (*need.required, *need.nice)} < codes
    assert all(s.confirmed_level is None for s in stuffer.skills)
    assert INJECTION in stuffer.about
    # Грейд подтверждён: иначе правило "неподтверждённый грейд ниже" решило бы за ранжировщик.
    assert stuffer.grade_source == "test"


def test_cut_kinds_break_exactly_one_hard_condition() -> None:
    specializations = {need.specialization for need in NEEDS}
    for need in NEEDS:
        above = cand(need.key, Kind.SALARY_ABOVE)
        assert above.salary[0] > need.salary[1]
        assert above.category == need.category
        other = cand(need.key, Kind.OTHER_SPEC)
        assert other.category.split(".")[0] not in specializations
        assert other.salary == cand(need.key, Kind.IDEAL).salary


def test_summary_names_confirmed_skills_by_their_reference_names() -> None:
    named = build({"python": "Python"})
    ideal = next(c for c in named.of("backend") if c.kind == Kind.IDEAL)
    assert "Python — уровень 5 из 5" in ideal.summary
    stuffer = next(c for c in named.of("backend") if c.kind == Kind.STUFFER)
    assert "Python" in stuffer.about and "не подтверждён" in stuffer.summary
