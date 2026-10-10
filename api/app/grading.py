from datetime import timedelta

from sqlalchemy import select

from app.db import enums
from app.db.models.assess import GradeAssignment, TestSession
from app.db.models.backend import TestBridge
from app.db.models.profile import Candidate
from app.db.models.ref import Category, Grade
from app.security import now


def claimed_passed(result, claimed):
    if result.get("claimed_grade") != claimed:
        raise ValueError("Результат относится к другому заявленному грейду")
    return result.get("outcome") != "insufficient" and any(
        item.get("grade") == claimed and item.get("confirmed") is True
        for item in result.get("levels", [])
    )


async def apply_result(session, local_id, remote_result):
    local = await session.get(TestSession, local_id)
    candidate = await session.get(Candidate, local.candidate_id, with_for_update=True)
    await session.refresh(local, with_for_update=True)
    bridge = await session.get(TestBridge, local_id, with_for_update=True)
    if bridge.applied_at or local.status in (
        enums.SessionStatus.CANCELLED,
        enums.SessionStatus.EXPIRED,
    ):
        return
    if str(remote_result.get("session_id")) != str(bridge.remote_id):
        raise ValueError("Результат относится к другой сессии")
    passed = claimed_passed(remote_result, bridge.grade)
    bridge.result = remote_result
    bridge.applied_at = now()
    local.finished_at = now()
    if remote_result.get("outcome") == "insufficient":
        local.status = enums.SessionStatus.CANCELLED
        bridge.state = "insufficient"
        return
    local.status = enums.SessionStatus.SCORED
    local.outcome = enums.SessionOutcome.PASSED if passed else enums.SessionOutcome.FAILED
    bridge.state = "scored"
    score = remote_result.get("score", {}).get("score")
    local.total_score = score if isinstance(score, (int, float)) and 0 <= score <= 1 else 0
    local.max_score = 1
    if passed:
        category = await session.scalar(
            select(Category).where(
                Category.specialization_id == local.specialization_id,
                Category.grade_id == local.claimed_grade_id,
            )
        )
        grade = await session.get(Grade, local.claimed_grade_id)
        current = await session.scalar(
            select(GradeAssignment).where(
                GradeAssignment.candidate_id == candidate.user_id, GradeAssignment.is_current
            )
        )
        if current:
            current.is_current = False
            await session.flush()
        session.add(
            GradeAssignment(
                candidate_id=candidate.user_id,
                category_id=category.id,
                source=enums.GradeSource.TEST,
                test_session_id=local.id,
                next_change_allowed_at=now() + timedelta(days=grade.cooldown_days),
                is_current=True,
            )
        )
        candidate.current_category_id = category.id
        local.assigned_category_id = category.id
        candidate.profile_refreshed_at = now()
    if isinstance(score, (int, float)) and 0 <= score <= 1:
        local.total_score, local.max_score = score, 1
        if passed:
            candidate.profile_strength = score * 100
    if local.total_score is None:
        local.total_score, local.max_score = 0, 1
