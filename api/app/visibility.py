from fastapi import HTTPException
from sqlalchemy import exists, select

from app.common import record
from app.db.enums import ConsentKind, GradeSource, LinkKind
from app.db.models.assess import GradeAssignment
from app.db.models.core import User, UserConsent
from app.db.models.hire import ContactDisclosure
from app.db.models.profile import (
    Candidate,
    CandidateLink,
    CandidatePreferences,
    CandidatePrivacy,
    CandidateSkill,
    FspAchievement,
    FspEvent,
)
from app.db.models.ref import Category, Skill
from app.security import eligible


def contact_access(disclosed: bool, mode: bool, preference: bool, consent: bool) -> bool:
    return disclosed or (mode and preference and consent)


def safe_match_result(row: dict, card: dict) -> dict:
    source = (row.get("factors") or {}).get("items", {})
    allowed = {"stack", "grade", "percentile", "freshness"}
    if "preferences" in card:
        allowed.add("salary")
    if "fsp_achievements" in card:
        allowed.add("fsp")
    factors = {
        name: {"value": value["value"], "applicable": value.get("applicable", False)}
        for name, value in source.items()
        if name in allowed
        and isinstance(value, dict)
        and isinstance(value.get("value"), (int, float))
    }
    return {
        "candidate_id": row["candidate_id"],
        "rank": row["rank"],
        "factors": factors,
        "final_score": row.get("final_score"),
        "candidate": card,
        "explanation": "Грейд подтверждён тестом"
        if card["grade_confirmed"]
        else "Грейд пока не подтверждён",
    }


async def has_consent(session, user_id, kind):
    return bool(
        await session.scalar(
            select(
                exists().where(
                    UserConsent.user_id == user_id,
                    UserConsent.kind == kind,
                    UserConsent.revoked_at.is_(None),
                )
            )
        )
    )


async def searchable(session, candidate, settings):
    privacy = await session.get(CandidatePrivacy, candidate.user_id)
    user = await session.get(User, candidate.user_id)
    return bool(
        privacy
        and privacy.is_searchable
        and user
        and eligible(user, settings)
        and candidate.is_open_to_offers
        and await has_consent(session, user.id, ConsentKind.PROFILE_PUBLICATION)
        and await has_consent(session, user.id, ConsentKind.PD_PROCESSING)
    )


async def card(session, candidate_id, settings, company_id=None, owner=False, relationship=False):
    candidate = await session.get(Candidate, candidate_id)
    if candidate is None:
        raise HTTPException(404, "Профиль не найден")
    visible = await searchable(session, candidate, settings)
    if not owner and not visible and not relationship:
        raise HTTPException(404, "Профиль не найден")
    privacy = await session.get(CandidatePrivacy, candidate_id)
    grade = await session.scalar(
        select(GradeAssignment).where(
            GradeAssignment.candidate_id == candidate_id, GradeAssignment.is_current
        )
    )
    category = (
        await session.get(Category, candidate.current_category_id)
        if candidate.current_category_id
        else None
    )
    result = {
        "user_id": candidate.user_id,
        "category": record(category) if category else None,
        "grade_confirmed": bool(grade and grade.source == GradeSource.TEST),
        "profile_strength": candidate.profile_strength,
    }
    if not owner and not visible:
        return {"user_id": candidate.user_id, "profile_hidden": True}
    for flag, fields in {
        "show_name": ["first_name", "last_name", "middle_name"],
        "show_city": ["city", "relocation", "work_format"],
        "show_experience": ["experience_months"],
        "show_about": ["about"],
    }.items():
        if owner or (privacy and getattr(privacy, flag)):
            result.update({field: getattr(candidate, field) for field in fields})
    disclosed = bool(
        company_id
        and privacy
        and privacy.show_contacts
        and await session.scalar(
            select(
                exists().where(
                    ContactDisclosure.candidate_id == candidate_id,
                    ContactDisclosure.company_id == company_id,
                    ContactDisclosure.revoked_at.is_(None),
                )
            )
        )
    )
    contacts = owner or contact_access(
        disclosed,
        settings.allow_public_contacts,
        bool(privacy and privacy.public_contacts),
        bool(privacy and privacy.public_contacts_consented_at),
    )
    if contacts:
        user = await session.get(User, candidate_id)
        result["contacts"] = {"email": user.email, "phone": candidate.phone}
    links = (
        await session.scalars(
            select(CandidateLink).where(CandidateLink.candidate_id == candidate_id)
        )
    ).all()
    result["links"] = [
        record(link, ["candidate_id", "created_at"])
        for link in links
        if owner
        or (
            (contacts if link.kind not in (LinkKind.GITHUB, LinkKind.PORTFOLIO) else link.is_public)
            and (link.kind != LinkKind.GITHUB or privacy.show_github)
        )
    ]
    skills = (
        await session.execute(
            select(CandidateSkill, Skill)
            .join(Skill)
            .where(CandidateSkill.candidate_id == candidate_id)
        )
    ).all()
    result["skills"] = [
        {
            "code": skill.code,
            "name": skill.name,
            "self_level": value.self_level,
            "confirmed_level": value.confirmed_level,
        }
        for value, skill in skills
    ]
    if owner or (privacy and privacy.show_salary):
        preference = await session.get(CandidatePreferences, candidate_id)
        result["preferences"] = record(preference, ["candidate_id"]) if preference else None
        if not owner and result["preferences"]:
            result["preferences"].pop("negative_prompt", None)
            result["preferences"].pop("positive_prompt", None)
    if owner or (
        privacy
        and privacy.show_fsp
        and await has_consent(session, candidate_id, ConsentKind.FSP_DATA_SYNC)
    ):
        achievements = (
            await session.scalars(
                select(FspAchievement).where(
                    FspAchievement.candidate_id == candidate_id,
                    FspAchievement.confirmed_at.is_not(None),
                )
            )
        ).all()
        result["fsp_achievements"] = []
        for item in achievements:
            event = await session.get(FspEvent, item.event_id)
            result["fsp_achievements"].append(
                {
                    **record(item, ["raw", "candidate_id"]),
                    "event_title": event.title,
                    "demo": bool(item.raw and item.raw.get("demo")),
                }
            )
    if owner:
        result["profile"] = record(candidate, ["search_vector"])
        result["privacy"] = record(privacy) if privacy else None
    return result
