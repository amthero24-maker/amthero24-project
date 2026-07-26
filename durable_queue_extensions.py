"""Optional encrypted durable-work composition for inbound WhatsApp messages.

The feature is disabled by default so deployment remains backward compatible. Once the
operator configures a dedicated key and explicitly enables it, POST /webhook persists an
encrypted recovery envelope before acknowledging Meta.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import BackgroundTasks, Request
from fastapi.responses import JSONResponse

import idempotency_extensions as composed
from durable_queue import (
    DurableQueueRepository,
    QueueServiceError,
    queue_enabled,
    queue_poll_seconds,
    queue_status,
)
from queue_drain import release_processing_item
from runtime_lifecycle import (
    lifecycle,
    shutdown_grace_seconds,
    shutdown_retry_delay_seconds,
)

logger = logging.getLogger("amthero24.durable_queue")
core = composed.core
_QUEUE_REPOSITORY: DurableQueueRepository | None = None
_WORKER_TASK: asyncio.Task[None] | None = None
_WORKER_STOP: asyncio.Event | None = None


def _repository(store: Any | None = None) -> DurableQueueRepository:
    global _QUEUE_REPOSITORY
    target = store or core.store
    if _QUEUE_REPOSITORY is None or _QUEUE_REPOSITORY.store is not target:
        _QUEUE_REPOSITORY = DurableQueueRepository(target)
    return _QUEUE_REPOSITORY


def _settle_interrupted_item(message_id: str) -> None:
    """Complete terminal work or release unfinished work without exposing its envelope."""
    try:
        state = composed._repository(core.store).state(message_id) or {}
        if state.get("status") in {"sent", "failed"}:
            _repository(core.store).complete(message_id)
            return
        release_processing_item(
            core.store,
            message_id,
            delay_seconds=shutdown_retry_delay_seconds(),
            code="shutdown_interrupted",
        )
    except Exception:
        logger.exception("Unable to settle interrupted durable queue item")


async def _process_queue_message(message_id: str | None = None) -> bool:
    if not lifecycle.try_start_work():
        return False
    repository = _repository()
    claimed_id = ""
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
        claimed_id = item.message_id

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
        except asyncio.CancelledError:
            _settle_interrupted_item(item.message_id)
            raise
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
    except asyncio.CancelledError:
        if claimed_id:
            _settle_interrupted_item(claimed_id)
        raise
    finally:
        lifecycle.finish_work()


async def receive_webhook(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    if lifecycle.is_draining():
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

    for queued_id in queued_ids:
        background_tasks.add_task(_process_queue_message, queued_id)

    if unavailable:
        return JSONResponse(
            {"status": "unavailable"},
            status_code=503,
            headers={"Retry-After": "10", "Cache-Control": "no-store"},
        )
    return JSONResponse({"status": "accepted"})


async def _worker_loop() -> None:
    stop = _WORKER_STOP
    while stop is not None and not stop.is_set() and not lifecycle.is_draining():
        try:
            processed = await _process_queue_message()
            if not processed:
                try:
                    _repository().cleanup()
                except Exception:
                    logger.exception("Durable queue cleanup failed")
                try:
                    await asyncio.wait_for(stop.wait(), timeout=queue_poll_seconds())
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Durable queue worker iteration failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=queue_poll_seconds())
            except TimeoutError:
                pass


async def _start_worker() -> None:
    global _WORKER_TASK, _WORKER_STOP
    if not queue_enabled():
        return
    status = queue_status(core.store)
    if status != "configured":
        logger.error("Durable queue enabled but not ready", extra={"status": status})
        return
    if _WORKER_TASK is None or _WORKER_TASK.done():
        _WORKER_STOP = asyncio.Event()
        _WORKER_TASK = asyncio.create_task(_worker_loop(), name="amthero24-durable-queue")


async def _stop_worker() -> None:
    global _WORKER_TASK, _WORKER_STOP
    lifecycle.begin_draining()
    stop = _WORKER_STOP
    if stop is not None:
        stop.set()
    task = _WORKER_TASK
    if task is None:
        _WORKER_STOP = None
        return

    idle = await lifecycle.wait_for_idle(shutdown_grace_seconds())
    if not idle and not task.done():
        task.cancel()
    try:
        await asyncio.wait_for(task, timeout=2)
    except TimeoutError:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    except asyncio.CancelledError:
        pass
    _WORKER_TASK = None
    _WORKER_STOP = None


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
