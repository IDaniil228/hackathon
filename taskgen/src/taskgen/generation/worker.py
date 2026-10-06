"""Фоновая генерация заданий внутри процесса сервиса.

Очередь — это сами строки tasks со статусом pending, поэтому внешний брокер не нужен, а
рестарт ничего не теряет: при старте приложения resume() снова запускает сессии в статусе
generating. Задача asyncio на сессию живёт только в памяти процесса. Предполагается один
процесс сервиса: два процесса с общей БД сгенерировали бы одно задание дважды, и записал
бы его тот, кто успел первым.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy import select

from taskgen.contracts import SessionStatus
from taskgen.db import TestSession
from taskgen.generation import service
from taskgen.generation.service import Generator, SessionAbortedError, SessionFailedError

logger = logging.getLogger(__name__)

INTERNAL_FAILURE = "Внутренняя ошибка генерации; создайте новую сессию"


class GenerationWorker:
    def __init__(self, state: Any) -> None:
        # Зависимости берутся из state в момент работы: тесты и встраивание подменяют их
        # после старта приложения.
        self._state = state
        self._jobs: dict[uuid.UUID, asyncio.Task[None]] = {}

    def generator(self) -> Generator:
        return Generator.from_state(self._state)

    async def start(self, session_id: uuid.UUID) -> None:
        """Запускает генерацию сессии и ждёт, пока первое задание будет готово.

        Остальные задания генерируются в фоне. Ожидание защищено от отмены: если клиент
        отключится, генерация первого задания и всей сессии продолжится. SessionFailedError —
        первое задание не создано, и сессия failed.
        """
        first: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._spawn(session_id, first)
        await asyncio.shield(first)

    def schedule(self, session_id: uuid.UUID) -> None:
        self._spawn(session_id, None)

    async def resume(self) -> int:
        """Снова запускает сессии, которые остались в generating после остановки сервиса."""
        async with self._state.sessionmaker() as db:
            ids = list(
                await db.scalars(
                    select(TestSession.id).where(TestSession.status == SessionStatus.GENERATING)
                )
            )
        for session_id in ids:
            self.schedule(session_id)
        if ids:
            logger.info("Подхвачено незавершённых сессий: %d", len(ids))
        return len(ids)

    @property
    def busy(self) -> bool:
        return bool(self._jobs)

    async def wait_idle(self) -> None:
        """Ждёт, пока закончатся все сессии, включая запущенные во время ожидания."""
        while self._jobs:
            await asyncio.gather(*list(self._jobs.values()), return_exceptions=True)

    async def aclose(self) -> None:
        # Отменённые задания остаются pending и подхватываются при следующем старте.
        jobs = list(self._jobs.values())
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)

    def _spawn(self, session_id: uuid.UUID, first: asyncio.Future[None] | None) -> None:
        if session_id in self._jobs:
            # Сессия уже генерируется; ждать её первого задания повторно незачем.
            if first is not None:
                first.set_result(None)
            return
        job = asyncio.create_task(self._run(session_id, first), name=f"session-{session_id}")
        self._jobs[session_id] = job
        job.add_done_callback(lambda _job: self._jobs.pop(session_id, None))

    async def _run(self, session_id: uuid.UUID, first: asyncio.Future[None] | None) -> None:
        gen = self.generator()
        try:
            pending = await service.pending_tasks(gen, session_id)
            # Ждать стоит только первое по порядку задание: если его выдал пул, ответ уже
            # можно отдавать.
            if first is not None and pending and pending[0].position == 1:
                await service.run_task(gen, pending.pop(0).id)
            _resolve(first)
            # Задания стартуют по порядку позиций, и ограничитель LLM пропускает их в том же
            # порядке: кандидат раньше получает задание 2, чем 5.
            async with asyncio.TaskGroup() as group:
                for task in pending:
                    group.create_task(service.run_task(gen, task.id))
            await service.finish_session(gen, session_id)
        except* SessionAbortedError as errors:
            error = errors.exceptions[0]
            assert isinstance(error, SessionAbortedError)
            await service.finish_session(gen, session_id, failure=error.reason)
            _resolve(first, SessionFailedError(session_id, error.cause))
        except* Exception as errors:
            logger.error("Генерация сессии %s упала", session_id, exc_info=errors.exceptions[0])
            await service.finish_session(gen, session_id, failure=INTERNAL_FAILURE)
            _resolve(first, errors.exceptions[0])
        finally:
            if first is not None and not first.done():
                first.cancel()


def _resolve(first: asyncio.Future[None] | None, error: BaseException | None = None) -> None:
    if first is None or first.done():
        return
    if error is None:
        first.set_result(None)
    else:
        first.set_exception(error)
