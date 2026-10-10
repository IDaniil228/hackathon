"""Machine summary кандидата из taskgen: GET /candidates/{candidate_ref}/summary.

candidate_ref в taskgen равен profile.candidates.user_id. Summary — необязательная часть
карточки: без неё кандидат остаётся в подборе, поэтому недоступный taskgen даёт None и
предупреждение в журнале, а не ошибку прогона.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Sequence
from typing import Protocol

import httpx

from matcher.config import TaskgenSettings

logger = logging.getLogger(__name__)


class SummarySource(Protocol):
    async def summary(self, candidate_ref: str) -> str | None: ...


class TaskgenSummaries:
    def __init__(
        self, settings: TaskgenSettings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        if settings.base_url is None:
            raise ValueError("taskgen.base_url не задан")
        headers = {}
        if settings.api_key is not None:
            headers["Authorization"] = f"Bearer {settings.api_key.get_secret_value()}"
        self._http = httpx.AsyncClient(
            base_url=settings.base_url.rstrip("/"),
            headers=headers,
            timeout=settings.timeout_seconds,
            transport=transport,
        )

    async def summary(self, candidate_ref: str) -> str | None:
        try:
            response = await self._http.get(f"/candidates/{candidate_ref}/summary")
        except httpx.HTTPError as error:
            logger.warning("taskgen недоступен, карточка %s без summary: %s", candidate_ref, error)
            return None
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            logger.warning(
                "taskgen ответил HTTP %s на summary %s, карточка без summary",
                response.status_code,
                candidate_ref,
            )
            return None
        return response.json().get("text") or None

    async def aclose(self) -> None:
        await self._http.aclose()


async def summaries_for(
    source: SummarySource | None, ids: Sequence[uuid.UUID], *, concurrency: int
) -> dict[uuid.UUID, str | None]:
    if source is None:
        return dict.fromkeys(ids)
    semaphore = asyncio.Semaphore(concurrency)

    async def one(candidate_id: uuid.UUID) -> str | None:
        async with semaphore:
            return await source.summary(str(candidate_id))

    texts = await asyncio.gather(*(one(cid) for cid in ids))
    return dict(zip(ids, texts, strict=True))
