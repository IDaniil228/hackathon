import asyncio
import hashlib
import json
import logging
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException
from sqlalchemy import select, text, update

from app.common import record
from app.db.enums import SessionStatus
from app.db.models.assess import TestSession
from app.db.models.backend import ServiceOperation, TestBridge
from app.grading import apply_result
from app.security import hash_token, now

logger = logging.getLogger(__name__)


def operation_out(row):
    return record(row, ["payload", "fingerprint", "response", "key"])


def require_service(app, service):
    if not getattr(app.state.settings, f"{service}_client_key"):
        raise HTTPException(503, {"code": "service_not_configured", "service": service})


async def queue_operation(session, user, kind, key, payload, resource_id=None):
    await session.refresh(user, with_for_update=True)
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    row = await session.scalar(
        select(ServiceOperation).where(
            ServiceOperation.owner_id == user.id,
            ServiceOperation.kind == kind,
            ServiceOperation.key == key,
        )
    )
    if row:
        if row.fingerprint != fingerprint:
            raise HTTPException(409, "Ключ идемпотентности уже использован для другого запроса")
        return row
    row = ServiceOperation(
        id=uuid4(),
        owner_id=user.id,
        kind=kind,
        key=key,
        fingerprint=fingerprint,
        resource_id=resource_id,
        payload=payload,
    )
    session.add(row)
    await session.flush()
    return row


async def call_service(app, service, method, path, **kwargs):
    require_service(app, service)
    settings = app.state.settings
    key = getattr(settings, f"{service}_client_key")
    if not key:
        raise HTTPException(503, f"Сервис {service} не настроен")
    response = await app.state.http.request(
        method,
        getattr(settings, f"{service}_url").rstrip("/") + path,
        headers={"Authorization": f"Bearer {key}"},
        **kwargs,
    )
    if response.status_code >= 400:
        code = response.status_code if response.status_code in (404, 409, 422) else 502
        raise HTTPException(
            code, {"code": "upstream_rejected", "service": service, "status": response.status_code}
        )
    try:
        return response.json()
    except ValueError as error:
        raise HTTPException(502, "Сервис вернул некорректный ответ") from error


async def process_operation(app, operation_id):
    async with app.state.sessionmaker() as session:
        row = await session.get(ServiceOperation, operation_id, with_for_update=True)
        if row is None or row.state not in ("queued", "waiting_external", "reconciling"):
            return
        waiting = row.state == "waiting_external"
        reconcile = row.state == "reconciling"
        if row.kind == "test_score":
            bridge = await session.get(TestBridge, row.resource_id)
            if bridge.applied_at:
                row.state, row.payload = "succeeded", None
                await session.commit()
                return
        payload = row.payload
        if row.kind != "test_score":
            metadata = dict(row.response or {})
            credential_hash = hash_token(
                getattr(app.state.settings, f"{payload['service']}_client_key")
            )
            if metadata.get("credential_hash", credential_hash) != credential_hash:
                row.state, row.error = "uncertain", "client_key_changed"
                await session.commit()
                return
            metadata.update(credential_hash=credential_hash, protocol="request_ref")
            row.response = metadata
        row.state, row.updated_at = "running", now()
        await session.commit()
    try:
        if row.kind == "test_score":
            try:
                result = await call_service(
                    app, "taskgen", "GET", f"/sessions/{row.remote_id}/result"
                )
            except HTTPException as error:
                if error.status_code != 409:
                    raise
                if waiting:
                    async with app.state.sessionmaker() as session:
                        current = await session.get(ServiceOperation, operation_id)
                        current.state = "waiting_external"
                        current.updated_at = now()
                        await session.commit()
                    return
                result = await call_service(
                    app, "taskgen", "POST", f"/sessions/{row.remote_id}/score"
                )
        else:
            result = None
            if reconcile:
                suffix = "session" if row.kind == "test_create" else "run"
                try:
                    receipt = await call_service(
                        app, payload["service"], "GET", f"/requests/{row.id}/{suffix}"
                    )
                    if row.kind == "test_create":
                        result = await call_service(
                            app, "taskgen", "GET", f"/sessions/{receipt['session_id']}"
                        )
                    else:
                        result = receipt
                except HTTPException as error:
                    if error.status_code != 404:
                        raise
            if result is None:
                result = await call_service(
                    app,
                    payload["service"],
                    "POST",
                    payload["path"],
                    json={**payload["body"], "request_ref": str(row.id)},
                )
        async with app.state.sessionmaker() as session:
            current = await session.get(ServiceOperation, operation_id, with_for_update=True)
            if row.kind == "test_create":
                if result.get("candidate_ref") != str(row.owner_id):
                    raise ValueError("Сервис вернул чужую сессию")
                bridge = await session.get(TestBridge, row.resource_id)
                if result.get("area") != bridge.area or result.get("claimed_grade") != bridge.grade:
                    raise ValueError("Сервис вернул сессию другой категории")
                bridge.remote_id = UUID(result["id"])
                bridge.state = result["status"]
                current.remote_id = bridge.remote_id
            elif row.kind == "test_score":
                await apply_result(session, row.resource_id, result)
            else:
                current.remote_id = UUID(result["run_id"])
            current.state, current.updated_at = "succeeded", now()
            current.error = None
            current.payload = None
            await session.commit()
    except Exception as error:
        async with app.state.sessionmaker() as session:
            current = await session.get(ServiceOperation, operation_id, with_for_update=True)
            ambiguous = isinstance(error, (httpx.TransportError, ValueError)) or (
                isinstance(error, HTTPException) and error.status_code >= 500
            )
            current.state = (
                ("waiting_external" if row.kind == "test_score" else "reconciling")
                if ambiguous
                else "failed"
            )
            current.error = "upstream_uncertain" if ambiguous else "upstream_rejected"
            current.updated_at = now()
            if current.state == "failed":
                current.payload = None
            if row.kind == "test_create":
                bridge = await session.get(TestBridge, row.resource_id)
                if current.state == "failed":
                    local = await session.get(TestSession, row.resource_id, with_for_update=True)
                    local.status = SessionStatus.CANCELLED
                bridge.state = current.state
            await session.commit()
        diagnostic = getattr(getattr(error, "orig", None), "diag", None)
        logger.warning(
            "Операция %s: %s, ошибка %s, ограничение %s",
            operation_id,
            current.state,
            type(error).__name__,
            getattr(diagnostic, "constraint_name", None),
        )


async def worker(app):
    async with app.state.engine.connect() as connection:
        locked = await connection.scalar(
            text("SELECT pg_try_advisory_lock(hashtextextended('fsp-api-worker', 0))")
        )
        await connection.commit()
        if not locked:
            logger.error("Другой процесс API уже обслуживает очередь этой БД")
            return
        try:
            await worker_loop(app)
        finally:
            await connection.execute(
                text("SELECT pg_advisory_unlock(hashtextextended('fsp-api-worker', 0))")
            )
            await connection.commit()


async def worker_loop(app):
    async with app.state.sessionmaker() as session:
        await session.execute(
            update(ServiceOperation)
            .where(ServiceOperation.state == "running")
            .values(state="uncertain", error="process_interrupted")
        )
        await session.execute(
            update(ServiceOperation)
            .where(ServiceOperation.kind == "test_score", ServiceOperation.state == "uncertain")
            .values(state="waiting_external")
        )
        await session.execute(
            update(ServiceOperation)
            .where(
                ServiceOperation.state == "uncertain",
                ServiceOperation.response["protocol"].astext == "request_ref",
            )
            .values(state="reconciling")
        )
        await session.commit()
    while True:
        try:
            async with app.state.sessionmaker() as session:
                ids = (
                    await session.scalars(
                        select(ServiceOperation.id)
                        .where(
                            ServiceOperation.state.in_(
                                ["queued", "waiting_external", "reconciling"]
                            )
                        )
                        .order_by(ServiceOperation.updated_at, ServiceOperation.created_at)
                        .limit(10)
                    )
                ).all()
            limit = asyncio.Semaphore(4)

            async def bounded(operation_id, semaphore=limit):
                async with semaphore:
                    await process_operation(app, operation_id)

            await asyncio.gather(*(bounded(operation_id) for operation_id in ids))
        except Exception:
            logger.exception("Не удалось обработать очередь API")
        await asyncio.sleep(2)
