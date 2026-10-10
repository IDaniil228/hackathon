import pytest

from app.grading import claimed_passed


def test_promotion_does_not_require_accepting_higher_grade():
    result = {
        "claimed_grade": "middle",
        "assigned_grade": "senior",
        "outcome": "upgraded",
        "levels": [{"grade": "middle", "confirmed": True}, {"grade": "senior", "confirmed": True}],
    }
    assert claimed_passed(result, "middle")


def test_lower_assignment_and_insufficient_never_confirm_claimed():
    assert not claimed_passed(
        {
            "claimed_grade": "middle",
            "assigned_grade": "junior",
            "outcome": "downgraded",
            "levels": [
                {"grade": "junior", "confirmed": True},
                {"grade": "middle", "confirmed": False},
            ],
        },
        "middle",
    )
    assert not claimed_passed(
        {
            "claimed_grade": "middle",
            "outcome": "insufficient",
            "levels": [{"grade": "middle", "confirmed": True}],
        },
        "middle",
    )


def test_foreign_claimed_result_rejected():
    with pytest.raises(ValueError):
        claimed_passed({"claimed_grade": "senior", "outcome": "passed", "levels": []}, "middle")
