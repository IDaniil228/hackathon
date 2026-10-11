from datetime import UTC, datetime

from app.db.seeds.synthetic import make_profiles


def test_professional_profiles_are_repeatable_and_diverse():
    reference = datetime(2026, 10, 11, tzinfo=UTC)
    first = make_profiles(1200, 42, reference)
    assert first == make_profiles(1200, 42, reference)
    assert first != make_profiles(1200, 43, reference)
    assert len({p["id"] for p in first}) == 1200
    assert len({p["about"] for p in first}) > 100
    assert all(p["salary_min"] <= p["salary_max"] for p in first)
    assert all(p["email"].endswith("@scepka.example") for p in first)
    assert sum(p["specialization"] == "backend" for p in first) > 30
