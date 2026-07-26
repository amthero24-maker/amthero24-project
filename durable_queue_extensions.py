"""Optional encrypted durable-work composition for inbound WhatsApp messages.

The feature is disabled by default so deployment remains backward compatible. Once the
operator configures a dedicated key and explicitly enables it, POST /webhook persists an
encrypted recovery envelope before acknowledging Meta. Processing leases are owned by
one process and released safely during Railway handoff.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any
from uuid import uuid4

from fastapi import BackgroundTasks, Request
from fastapi.responses import JSONResponse

import durable_queue as queue_module
import idempotency_extensions as composed
from deployment_lifecycle import lifecycle
from durable_queue import (
    DurableQueueRepository,
    QueueItem,
    QueueServiceError,
    queue_enabled,
    queue_poll_seconds,
    queue_status,
)

logger = logging.getLogger("amthero24.durable_queue")
core = composed.core
_QUEUE_REPOSITORY: DurableQueueRepository | None = None
_WORKER_TASK: asyncio.Task[None] | None = None
_WORKER_ID = uuid4().hex


class OwnedDurableQueueRepository(DurableQueueRepository):
    """Bind processing leases to one process so shutdown releases only its work."""

    def _initialize_postgres_schema(self) -> None:
        super()._initialize_postgres_schema()
        with self.store.pool.connection() as connection:
            connection.execute(
                "ALTER TABLE inbound_work_queue ADD COLUMN IF NOT EXISTS lease_owner TEXT"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS inbound_work_queue_owner_idx "
                "ON inbound_work_queue (lease_owner) WHERE status = 'processing'"
            )

    def claim(self, message_id: str | None = None, *, now=None) -> QueueItem | None:
        self._require_postgres()
        current = queue_module._now(now)
        lease_until = current + queue_module._processing_lease()
        requested = str(message_id or "").strip() or None
        max_attempts = queue_module._max_attempts()
        with self.store.pool.connection() as connection:
            connection.execute(
                """
                UPDATE inbound_work_queue
                SET status = 'dead', sender_ciphertext = '', media_id_ciphertext = NULL,
                    lease_until = NULL, lease_owner = NULL,
                    last_failure_code = 'max_attempts', updated_at = %s
                WHERE status IN ('queued', 'processing') AND attempt_count >= %s
                """,
                (current, max_attempts),
            )
            row = connection.execute(
                """
                WITH candidate AS (
                    SELECT message_id
                    FROM inbound_work_queue
                    WHERE expires_at > %s
                      AND attempt_count < %s
                      AND (
                            (status = 'queued' AND available_at <= %s)
                         OR (status = 'processing' AND (lease_until IS NULL OR lease_until <= %s))
                      )
                      AND (%s::TEXT IS NULL OR message_id = %s)
                    ORDER BY available_at ASC, created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                ), updated AS (
                    UPDATE inbound_work_queue AS queue
                    SET status = 'processing', lease_until = %s, lease_owner = %s,
                        attempt_count = queue.attempt_count + 1, updated_at = %s
                    FROM candidate
                    WHERE queue.message_id = candidate.message_id
                    RETURNING queue.*
                )
                SELECT updated.message_id, updated.sender_ciphertext,
                       updated.media_id_ciphertext, updated.mime_type,
                       updated.attempt_count, messages.text,
                       messages.message_type, messages.status AS inbound_status
                FROM updated
                JOIN inbound_messages AS messages USING (message_id)
                """,
                (
                    current,
                    max_attempts,
                    current,
                    current,
                    requested,
                    requested,
                    lease_until,
                    _WORKER_ID,
                    current,
                ),
            ).fetchone()
        if not row:
            return None
        return QueueItem(
            message_id=str(row["message_id"]),
            sender=queue_module._decrypt(str(row["sender_ciphertext"])),
            text=str(row.get("text") or ""),
            message_type=str(row.get("message_type") or "text"),
            media_id=queue_module._decrypt(str(row["media_id_ciphertext"])) if row.get("media_id_ciphertext") else None,
            mime_type=str(row.get("mime_type") or "application/octet-stream"),
            inbound_status=str(row.get("inbound_status") or "processing"),
            attempt_count=int(row.get("attempt_count") or 0),
        )

    def release_owned(self, *, now=None) -> int:
        """Return only this process's unfinished leases to immediate retry."""
        self._require_postgres()
        current = queue_module._now(now)
        with self.store.pool.connection() as connection:
            result = connection.execute(
                """
                UPDATE inbound_work_queue
                SET status = 'queued', available_at = %s, lease_until = NULL,
                    lease_owner = NULL, last_failure_code = 'process_draining',
                    updated_at = %s
                WHERE status = 'processing' AND lease_owner = %s
                """,
                (current, current, _WORKER_ID),
            )
        return int(result.rowcount or 0)

    def complete(self, message_id: str, *, now=None) -> None:
        super().complete(message_id, now=now)
        self._clear_owner(message_id)

    def retry(self, message_id: str, code: str, *, now=None) -> str:
        state = super().retry(message_id, code, now=now)
        self._clear_owner(message_id)
        return state

    def dead_letter(self, message_id: str, code: str, *, now=None) -> None:
        super().dead_letter(message_id, code, now=now)
        self._clear_owner(message_id)

    def _clear_owner(self, message_id: str) -> None:
        with self.store.pool.connection() as connection:
            connection.execute(
                "UPDATE inbound_work_queue SET lease_owner = NULL WHERE message_id = %s",
                (str(message_id),),
            )


def _repository(store: Any | None = None) -> DurableQueueRepository:
    global _QUEUE_REPOSITORY
    target = store or core.store
    if _QUEUE_REPOSITORY is None or _QUEUE_REPOSITORY.store is not target:
        _QUEUE_REPOSITORY = OwnedDurableQueueRepository(target)
    return _QUEUE_REPOSITORY


async def _process_queue_message(message_id: str | None = None) -> bool:
    if not lifecycle.work_started():
        return False
    repository = _repository()
    try:
        try:
            item = repository.claim(message_id)
        except QueueServiceError as exc:
            logger.error("Unable to claim durable queue item", extra={"code": exc.code})
            if message_id:
                try:
                    repository.dead_letter(message_id, exc.code)
                except Exception:
                    logger.exception("Unable to dead-letter unreadable queue item")
            return False
        except Exception:
            logger.exception("Unexpected durable queue claim failure")
            return False

        if item is None:
            return False

        if item.inbound_status in {"sent", "failed"}:
            repository.complete(item.message_id)
            return True

        message = core.IncomingMessage(
            item.message_id,
            item.sender,
            item.text,
            item.message_type,
            item.media_id,
            item.mime_type,
        )
        try:
            await core.process_incoming(message)
        except Exception:
            logger.exception("Unhandled durable queue processing failure", extra={"message_id": item.message_id})
            repository.retry(item.message_id, "unhandled_processing_error")
            return True

        state = composed._repository(core.store).state(item.message_id) or {}
        if state.get("status") in {"sent", "failed"}:
            repository.complete(item.message_id)
        else:
            repository.retry(item.message_id, "processing_incomplete")
        return True
    finally:
        lifecycle.work_finished()


async def receive_webhook(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    if not lifecycle.snapshot().accepting_work:
        return JSONResponse(
            {"status": "draining"},
            status_code=503,
            headers={"Retry-After": "10", "Cache-Control": "no-store"},
        )
    if not queue_enabled():
        return await composed.receive_webhook(request, background_tasks)

    status = queue_status(core.store)
    if status != "configured":
        return JSONResponse(
            {"status": "unavailable"},
            status_code=503,
            headers={"Retry-After": "10", "Cache-Control": "no-store"},
        )

    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        return JSONResponse({"status": "accepted"})

    unavailable = False
    queued_ids: list[str] = []
    message_claims = composed._repository(core.store)
    queue = _repository(core.store)

    for message in core.extract_incoming_messages(payload):
        try:
            claimed = message_claims.claim(
                message.message_id,
                message.sender,
                message.text,
                message_type=message.message_type,
                media_id=message.media_id,
            )
            if not claimed:
                continue
            queue.enqueue(
                message.message_id,
                message.sender,
                media_id=message.media_id,
                mime_type=message.mime_type,
            )
            queued_ids.append(message.message_id)
        except Exception:
            unavailable = True
            logger.exception("Unable to persist durable inbound envelope", extra={"message_id": message.message_id})
            try:
                core.store.update_message_status(message.message_id, "failed")
            except Exception:
                logger.exception("Unable to release failed durable message claim")

    for message_id in queued_ids:
        background_tasks.add_task(_process_queue_message, message_id)

    if unavailable:
        return JSONResponse(
            {"status": "unavailable"},
            status_code=503,
            headers={"Retry-After": "10", "Cache-Control": "no-store"},
        )
    return JSONResponse({"status": "accepted"})


async def _worker_loop() -> None:
    while lifecycle.snapshot().accepting_work:
        try:
            processed = await _process_queue_message()
            if not processed:
                try:
                    _repository().cleanup()
                except Exception:
                    logger.exception("Durable queue cleanup failed")
                await asyncio.sleep(queue_poll_seconds())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Durable queue worker iteration failed")
            await asyncio.sleep(queue_poll_seconds())


async def _start_worker() -> None:
    global _WORKER_TASK
    if not queue_enabled():
        return
    status = queue_status(core.store)
    if status != "configured":
        logger.error("Durable queue enabled but not ready", extra={"status": status})
        return
    if _WORKER_TASK is None or _WORKER_TASK.done():
        _WORKER_TASK = asyncio.create_task(_worker_loop(), name="amthero24-durable-queue")


async def _stop_worker() -> None:
    global _WORKER_TASK
    task = _WORKER_TASK
    _WORKER_TASK = None
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await lifecycle.wait_for_idle()
    try:
        repository = _repository()
        if isinstance(repository, OwnedDurableQueueRepository):
            repository.release_owned()
    except Exception:
        logger.exception("Unable to release owned queue leases during drain")


def _install_webhook_route() -> None:
    remaining = []
    for route in core.app.router.routes:
        methods = set(getattr(route, "methods", set()) or set())
        if getattr(route, "path", None) == "/webhook" and "POST" in methods:
            continue
        remaining.append(route)
    core.app.router.routes[:] = remaining
    core.app.add_api_route(
        "/webhook",
        receive_webhook,
        methods=["POST"],
        include_in_schema=False,
        name="receive_webhook_durable",
    )


_repository()
_install_webhook_route()
core.app.router.on_startup.append(_start_worker)
core.app.router.on_shutdown.append(_stop_worker)

app = composed.app
store = composed.store
