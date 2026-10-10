from matcher.profile_text import Card, SkillLevel, render_profile, source_hash

FULL = Card(
    category="Backend-разработчик, Middle",
    category_confirmed=True,
    confirmed_skills=(SkillLevel("Python", 4), SkillLevel("PostgreSQL", 3)),
    claimed_skills=(SkillLevel("FastAPI", 4),),
    experience_months=40,
    about="Схема платёжных данных",
    positive_prompt="Сложная модель данных",
    negative_prompt="НЕГАТИВ-МАРКЕР поддержка монолита",
    github=("demo/pg-partitions: партиционирование",),
    fsp=("Кубок ФСП: 12 место из 64",),
    summary="Тест 1 из 1: заявленный грейд подтверждён.",
)


def test_full_card_carries_every_positive_field() -> None:
    text = render_profile(FULL)
    for fragment in (
        "Backend-разработчик, Middle",
        "Python",
        "PostgreSQL",
        "FastAPI",
        "40",
        "Схема платёжных данных",
        "Сложная модель данных",
        "demo/pg-partitions",
        "Кубок ФСП",
        "заявленный грейд подтверждён",
    ):
        assert fragment in text


def test_negative_prompt_never_enters_text() -> None:
    assert "НЕГАТИВ-МАРКЕР" not in render_profile(FULL)
    only_negative = Card(negative_prompt="НЕГАТИВ-МАРКЕР")
    assert render_profile(only_negative) == ""


def test_empty_fields_are_omitted() -> None:
    assert render_profile(Card()) == ""
    text = render_profile(Card(about="Только о себе"))
    assert text.count("\n") == 0 and "Только о себе" in text
    assert "Опыт" not in render_profile(Card(about="x", experience_months=None))


def test_confirmation_status_distinguishes_confirmed_from_claimed_grade() -> None:
    confirmed = render_profile(Card(category="Backend, Middle", category_confirmed=True))
    unconfirmed = render_profile(Card(category="Backend, Middle", category_confirmed=False))
    assert confirmed != unconfirmed
    assert "не подтвержд" in unconfirmed and "не подтвержд" not in confirmed


def test_confirmed_and_claimed_skills_are_told_apart() -> None:
    as_confirmed = render_profile(Card(confirmed_skills=(SkillLevel("Go", 3),)))
    as_claimed = render_profile(Card(claimed_skills=(SkillLevel("Go", 3),)))
    assert as_confirmed != as_claimed


def test_render_and_hash_are_deterministic() -> None:
    assert render_profile(FULL) == render_profile(FULL)
    assert source_hash(render_profile(FULL)) == source_hash(render_profile(FULL))
    assert source_hash("a") != source_hash("b")
    assert len(source_hash("a")) == 64
