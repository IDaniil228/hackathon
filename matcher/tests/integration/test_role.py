"""Права роли matcher из db-init.sql и сверка перечислений со схемой api."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from matcher.db import MatchStage, RunStatus
from tests.integration.conftest import DEMO_NEED


async def test_role_cannot_insert_into_profile(matcher_session: AsyncSession) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        await matcher_session.execute(
            text(
                "INSERT INTO profile.candidates (user_id, first_name, last_name) "
                "VALUES (:id, 'x', 'y')"
            ),
            {"id": uuid.uuid4()},
        )


async def test_role_cannot_update_hire(matcher_session: AsyncSession) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        await matcher_session.execute(text("UPDATE hire.employer_needs SET title = title"))


@pytest.mark.parametrize(
    "query",
    [
        "SELECT phone FROM profile.candidates",
        "SELECT birth_date FROM profile.candidates",
        "SELECT last_name FROM profile.candidates",
        "SELECT access_token_enc FROM profile.github_accounts",
        "SELECT raw_text FROM profile.resume_documents",
        "SELECT raw_payload FROM profile.fsp_profiles",
        "SELECT url FROM profile.candidate_links",
        "SELECT email FROM core.users",
    ],
)
async def test_role_cannot_read_personal_data(matcher_session: AsyncSession, query: str) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        await matcher_session.execute(text(query))


async def test_role_reads_api_schemas(matcher_session: AsyncSession) -> None:
    for table in (
        "ref.skills",
        "profile.candidates",
        "profile.github_repos",
        "assess.grade_assignments",
        "hire.employer_needs",
        "match.skill_relevance",
    ):
        assert await matcher_session.scalar(text(f"SELECT count(*) FROM {table}")) >= 0


async def test_role_writes_match(matcher_session: AsyncSession) -> None:
    run_id = await matcher_session.scalar(
        text(
            "INSERT INTO match.search_runs (need_id, status) VALUES (:need, 'queued') RETURNING id"
        ),
        {"need": uuid.UUID(DEMO_NEED)},
    )
    await matcher_session.execute(
        text("UPDATE match.search_runs SET status = 'running' WHERE id = :id"), {"id": run_id}
    )
    await matcher_session.execute(
        text("DELETE FROM match.search_runs WHERE id = :id"), {"id": run_id}
    )


async def test_enums_match_database(matcher_session: AsyncSession) -> None:
    for enum, pg_type in ((RunStatus, "match.run_status"), (MatchStage, "match.match_stage")):
        values = await matcher_session.scalar(text(f"SELECT enum_range(NULL::{pg_type})::text[]"))
        assert values == [member.value for member in enum]
