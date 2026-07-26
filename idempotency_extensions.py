"""Production webhook idempotency and retry composition.

The layer replaces only POST /webhook. Meta verification GET and every existing
conversation layer remain unchanged.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import BackgroundTasks, Request
from fastapi.responses import JSONResponse

import feedback_extensions as composed
from message_idempotency import MessageClaimRepository

logger = logging.getLogger("amthero24.idempotency")
core = composed.core
_MESSAGE_REPOSITORY: MessageClaimRepository | None = None


def _repository(store: Any | None = None) -> MessageClaimRepository:
    global _MESSAGE_REPOSITORY
    target = store or core.store
    if _MESSAGE_REPOSITORY is None or _MESSAGE_REPOSITORY.store is not target:
        _MESSAGE_REPOSITORY = MessageClaimRepository(target)
    return _MESSAGE_REPOSITORY


async def _process_claimed(message: core.IncomingMessage) -> None:
    """Guarantee that exceptions outside the core handler become retryable state."""
    try:
        await core.process_incoming(message)
    except Exception:
        logger.exception("Unhandled claimed-message failure", extra={"message_id": message.message_id})
        try:
            core.store.update_message_status(message.message_id, "failed")
        except Exception:
            logger.exception("Unable to release failed message claim", extra={"message_id": message.message_id})


async def receive_webhook(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    """Acknowledge only after every incoming message has a durable processing claim."""
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        logger.warning("Ignoring malformed webhook payload")
        return JSONResponse({"status": "accepted"})

    unavailable = False
    claimed_messages: list[core.IncomingMessage] = []
    repository = _repository()
    for message in core.extract_incoming_messages(payload):
        try:
            claimed = repository.claim(
                message.message_id,
                message.sender,
                message.text,
                message_type=message.message_type,
                media_id=message.media_id,
            )
        except Exception:
            unavailable = True
            logger.exception("Unable to durably claim webhook message", extra={"message_id": message.message_id})
            continue
        if claimed:
            claimed_messages.append(message)

    for message in claimed_messages:
        background_tasks.add_task(_process_claimed, message)

    if unavailable:
        return JSONResponse(
            {"status": "unavailable"},
            status_code=503,
            headers={"Retry-After": "5", "Cache-Control": "no-store"},
        )
    return JSONResponse({"status": "accepted"})


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
        name="receive_webhook_retry_safe",
    )


_repository()
_install_webhook_route()

app = composed.app
store = composed.store
