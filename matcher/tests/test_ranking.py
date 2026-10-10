import uuid

from matcher.factors import Factor
from matcher.ranking import (
    EXPLANATION_LIMIT,
    Ranked,
    Verdict,
    explore,
    needs_vectors,
    order,
    shortlist,
    template_explanation,
)


def ranked(n: int, *, confirmed: bool, factor: float, vector: float | None = None) -> Ranked:
    return Ranked(
        candidate_id=uuid.UUID(int=n),
        grade_confirmed=confirmed,
        factor_score=factor,
        vector_score=vector,
        factors={},
    )


def verdict(score: float) -> Verdict:
    return Verdict(score=score, need_negative_hits=[], candidate_negative_hits=[], sentences=[])


def test_confirmed_grade_goes_before_any_factor_score() -> None:
    strong_unconfirmed = ranked(1, confirmed=False, factor=0.99, vector=0.99)
    weak_confirmed = ranked(2, confirmed=True, factor=0.10, vector=0.10)
    assert [r.candidate_id.int for r in order([strong_unconfirmed, weak_confirmed])] == [2, 1]


def test_factor_score_then_vector_score_then_stable_id() -> None:
    items = [
        ranked(1, confirmed=True, factor=0.5, vector=0.2),
        ranked(2, confirmed=True, factor=0.7, vector=0.1),
        ranked(3, confirmed=True, factor=0.5, vector=0.9),
        ranked(4, confirmed=True, factor=0.5, vector=None),
        ranked(5, confirmed=True, factor=0.5, vector=None),
    ]
    assert [r.candidate_id.int for r in order(items)] == [2, 3, 1, 4, 5]
    assert order(list(reversed(items))) == order(items)


def test_llm_score_sits_between_confirmation_and_factor_score() -> None:
    low_factor = ranked(1, confirmed=True, factor=0.1)
    high_factor = ranked(2, confirmed=True, factor=0.9)
    low_factor.verdict, high_factor.verdict = verdict(0.8), verdict(0.2)
    assert [r.candidate_id.int for r in order([high_factor, low_factor])] == [1, 2]


def test_template_explanation_is_built_from_factor_lines_and_fits_column() -> None:
    factors = {
        "stack": Factor(1.0, "Стек: подтверждены Python"),
        "salary": Factor(0.5, "Вилка: не задана", applicable=False),
    }
    text = template_explanation(True, factors)
    assert "Стек: подтверждены Python" in text
    assert "Вилка" not in text
    assert "не подтвержд" in template_explanation(False, factors)
    huge = {"stack": Factor(1.0, "Стек: " + "навык, " * 1000)}
    assert len(template_explanation(True, huge)) <= EXPLANATION_LIMIT


def test_grade_confirmation_is_stated_once() -> None:
    grade = Factor(1.0, "Грейд: Middle, совпадает с потребностью, не подтверждён тестом")
    text = template_explanation(False, {"grade": grade})
    assert text.count("подтверждён тестом") == 1
    unused = Factor(0.5, "Грейд: в потребности не задан", applicable=False)
    assert "не подтверждён тестом" in template_explanation(False, {"grade": unused})


def test_exploration_is_reproducible_and_disjoint() -> None:
    rest = [uuid.UUID(int=n) for n in range(1, 21)]
    # Новизна: чем больше номер, тем новее профиль.
    created = {cid: cid.int for cid in rest}
    run_id = uuid.uuid4()
    first = explore(rest, created, run_id=run_id, fresh=3, random_count=3)
    again = explore(list(reversed(rest)), created, run_id=run_id, fresh=3, random_count=3)
    assert first == again
    assert first.fresh == [uuid.UUID(int=n) for n in (20, 19, 18)]
    assert len(first.random) == 3
    assert not set(first.fresh) & set(first.random)
    picks = {
        tuple(explore(rest, created, run_id=uuid.uuid4(), fresh=3, random_count=3).random)
        for _ in range(5)
    }
    assert len(picks) > 1


def test_small_pool_skips_vector_stage() -> None:
    pool = {uuid.UUID(int=n): n for n in range(1, 6)}
    picked = shortlist(pool, None, top_k=5, run_id=uuid.uuid4(), fresh=3, random_count=3)
    assert picked.vector_skipped
    assert sorted(picked.ranked) == sorted(pool)
    assert picked.exploration.all() == [] and picked.rest == []
    assert not needs_vectors(len(pool), top_k=5)
    assert needs_vectors(6, top_k=5)


def test_large_pool_takes_top_k_by_vector_then_explores_the_rest() -> None:
    pool = {uuid.UUID(int=n): n for n in range(1, 21)}
    # Ближе к запросу кандидаты с меньшим номером; у 20-го вектора нет.
    scores = {cid: 1.0 - cid.int / 100 for cid in pool if cid.int != 20}
    picked = shortlist(pool, scores, top_k=5, run_id=uuid.uuid4(), fresh=3, random_count=3)
    assert not picked.vector_skipped
    assert [cid.int for cid in picked.top] == [1, 2, 3, 4, 5]
    assert len(picked.exploration.all()) == 6
    assert set(picked.ranked) == set(picked.top) | set(picked.exploration.all())
    assert set(picked.rest) == set(pool) - set(picked.ranked)
    # Кандидат без вектора в top_k не попадает, но разведка может его взять.
    assert uuid.UUID(int=20) in picked.exploration.fresh


def test_exploration_takes_what_there_is() -> None:
    rest = [uuid.UUID(int=n) for n in range(1, 5)]
    picked = explore(rest, {c: c.int for c in rest}, run_id=uuid.uuid4(), fresh=3, random_count=3)
    assert len(picked.fresh) == 3 and len(picked.random) == 1
