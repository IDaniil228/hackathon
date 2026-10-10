import uuid

import httpx
import pytest
from pydantic import SecretStr

from matcher.config import TaskgenSettings
from matcher.summaries import TaskgenSummaries, summaries_for

KNOWN = uuid.UUID(int=1)
UNKNOWN = uuid.UUID(int=2)
BROKEN = uuid.UUID(int=3)


def handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer client-key"
    if request.url.path == f"/candidates/{KNOWN}/summary":
        return httpx.Response(200, json={"candidate_ref": str(KNOWN), "text": "Тест 1 из 1."})
    if request.url.path == f"/candidates/{BROKEN}/summary":
        return httpx.Response(500)
    return httpx.Response(404, json={"detail": "нет сессий"})


@pytest.fixture
def source() -> TaskgenSummaries:
    settings = TaskgenSettings(base_url="http://taskgen", api_key=SecretStr("client-key"))
    return TaskgenSummaries(settings, transport=httpx.MockTransport(handler))


async def test_summary_by_candidate_user_id_and_404_as_none(source: TaskgenSummaries) -> None:
    texts = await summaries_for(source, [KNOWN, UNKNOWN, BROKEN], concurrency=2)
    assert texts == {KNOWN: "Тест 1 из 1.", UNKNOWN: None, BROKEN: None}
    await source.aclose()


async def test_no_source_means_no_summaries() -> None:
    assert await summaries_for(None, [KNOWN], concurrency=1) == {KNOWN: None}
