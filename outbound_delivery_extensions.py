"""Top-level WhatsApp delivery-receipt composition.

The layer records Meta's returned message IDs as one-way hashes, processes signed status
webhooks, and exposes only aggregate delivery health through existing protected operator
reports.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from fastapi import BackgroundTasks, Request
from fastapi.responses import JSONResponse

import admin_extensions as admin_module
import durable_queue_extensions as webhook_module
import launch_extensions as launch_module
import privacy_engine as privacy_module
import queue_observability_extensions as composed
import whatsapp as whatsapp_module
from outbound_delivery import (
    DeliveryReceipt,
    OutboundDeliveryRepository,
    extract_delivery_receipts,
    extract_response_message_ids,
)
from outbound_delivery_observability import build_outbound_delivery_overview
from outbound_delivery_policy import augment_launch_report
from outbound_delivery_recovery import OutboundDeliveryRecoveryState

logger = logging.getLogger("amthero24.outbound_delivery")
core = composed.core
_ORIGINAL_ADMIN_BUILD_OVERVIEW = admin_module.build_overview
_ORIGINAL_BUILD_LAUNCH_REPORT = launch_module.build_launch_report
_ORIGINAL_PRIVACY_CLEANUP = privacy_module.cleanup_retention
_DELIVERY_REPOSITORY: OutboundDeliveryRepository | None = None
_DELIVERY_RECOVERY: OutboundDeliveryRecoveryState | None = None


def _repository(store: Any | None = None) -> OutboundDeliveryRepository:
    global _DELIVERY_REPOSITORY
    target = store or core.store
    if _DELIVERY_REPOSITORY is None or _DELIVERY_REPOSITORY.store is not target:
        _DELIVERY_REPOSITORY = OutboundDeliveryRepository(target)
    return _DELIVERY_REPOSITORY


def _recovery(store: Any | None = None) -> OutboundDeliveryRecoveryState:
    global _DELIVERY_RECOVERY
    target = store or core.store
    _repository(target)  # Ensure the per-message table exists before recovery bootstrap.
    if _DELIVERY_RECOVERY is None or _DELIVERY_RECOVERY.store is not target:
        _DELIVERY_RECOVERY = OutboundDeliveryRecoveryState(target)
    return _DELIVERY_RECOVERY


def record_receipts(
    repository: OutboundDeliveryRepository,
    receipts: Iterable[DeliveryReceipt],
) -> dict[str, int]:
    """Persist known receipts and their aggregate recovery evidence atomically per event."""
    result = {"updated": 0, "unknown": 0}
    recovery = _recovery(repository.store)
    for receipt in receipts:
        state = repository.record_receipt(receipt)
        if state == "unknown":
            result["unknown"] += 1
            continue
        recovery.record_receipt(receipt, resulting_status=state)
        result["updated"] += 1
    return result


def _install_send_tracking() -> None:
    current = whatsapp_module._post_message
    if getattr(current, "_amthero24_delivery_tracking", False):
        return

    async def tracked_post(payload: dict[str, Any]) -> dict[str, Any]:
        response = await current(payload)
        try:
            repository = _repository()
            kind = str(payload.get("type") or "unknown")
            for message_id in extract_response_message_ids(response):
                repository.record_accepted(message_id, message_kind=kind)
        except Exception:
            logger.exception("Unable to record outbound delivery acceptance")
        return response

    setattr(tracked_post, "_amthero24_delivery_tracking", True)
    whatsapp_module._post_message = tracked_post


async def receive_webhook(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        return await webhook_module.receive_webhook(request, background_tasks)

    receipts = extract_delivery_receipts(payload)
    if receipts:
        try:
            record_receipts(_repository(), receipts)
        except Exception:
            logger.exception("Unable to persist WhatsApp delivery receipts")
            return JSONResponse(
                {"status": "unavailable"},
                status_code=503,
                headers={"Retry-After": "10", "Cache-Control": "no-store"},
            )
    return await webhook_module.receive_webhook(request, background_tasks)


def _build_overview(store: Any, **kwargs: Any) -> dict[str, Any]:
    payload = _ORIGINAL_ADMIN_BUILD_OVERVIEW(store, **kwargs)
    payload["outbound_delivery"] = build_outbound_delivery_overview(
        store,
        now=kwargs.get("now"),
        recovery=_recovery(store).snapshot(),
    )
    return payload


def _build_launch_report(overview: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return augment_launch_report(_ORIGINAL_BUILD_LAUNCH_REPORT(overview, **kwargs), overview)


def _cleanup_retention(store: Any, **kwargs: Any) -> dict[str, int]:
    result = _ORIGINAL_PRIVACY_CLEANUP(store, **kwargs)
    result["outbound_delivery"] = _repository(store).cleanup(now=kwargs.get("now"))
    return result


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
        name="receive_webhook_with_delivery_receipts",
    )


admin_module.build_overview = _build_overview
launch_module.build_launch_report = _build_launch_report
privacy_module.cleanup_retention = _cleanup_retention
_install_send_tracking()
_install_webhook_route()

app = composed.app
store = composed.store
