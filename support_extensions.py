"""Human-support handoff composition and separately protected operator queue."""
from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

import admin_extensions as admin_module
import launch_extensions as composed
import privacy_engine as privacy_module
import privacy_extensions as privacy_composed
from support_handoff import (
    SupportRepository,
    cancelled_message,
    classify_category,
    classify_urgency,
    created_message,
    detect_support_intent,
    status_message,
    support_configured,
    support_enabled,
    unavailable_message,
)

core = composed.core
_ORIGINAL_PROCESS_INCOMING = core.process_incoming
_ORIGINAL_PRIVACY_DELETE = privacy_composed.delete_all_user_data
_ORIGINAL_PRIVACY_CLEANUP = privacy_module.cleanup_retention
_ORIGINAL_ADMIN_BUILD_OVERVIEW = admin_module.build_overview
_SUPPORT_REPOSITORY: SupportRepository | None = None


def _repository(store: Any | None = None) -> SupportRepository:
    global _SUPPORT_REPOSITORY
    target = store or core.store
    if _SUPPORT_REPOSITORY is None or _SUPPORT_REPOSITORY.store is not target:
        _SUPPORT_REPOSITORY = SupportRepository(target)
    return _SUPPORT_REPOSITORY


def _language(profile: dict[str, Any]) -> str:
    language = str(
        profile.get("preferred_language")
        if profile.get("memory_consent") == "granted"
        else profile.get("session_language") or profile.get("preferred_language") or "de"
    )
    return language if language in {"de", "ar", "en", "uk", "el"} else "de"


async def process_incoming(message: core.IncomingMessage) -> None:
    intent = detect_support_intent(message.text) if message.message_type == "text" else None
    if intent is None:
        await _ORIGINAL_PROCESS_INCOMING(message)
        return

    profile = core.store.get_user(message.sender)
    previous = _language(profile)
    language = core.detect_language(message.text, previous) if message.text.strip() else previous
    core.store.update_user(message.sender, {
        "session_language": language,
        "session_topic": "human_support",
        "session_expires_at": core._session_expiry(),
        "last_seen": core._now().isoformat(),
    })

    if not support_configured():
        await core._finish(message.message_id, unavailable_message(language), message.sender)
        return

    repository = _repository()
    if intent.action == "status":
        await core._finish(
            message.message_id,
            status_message(language, repository.latest_for_user(message.sender)),
            message.sender,
        )
        return
    if intent.action == "cancel":
        cancelled = repository.cancel_latest(message.sender)
        await core._finish(message.message_id, cancelled_message(language, cancelled is not None), message.sender)
        return

    ticket = repository.create(
        message.sender,
        language=language,
        category=classify_category(message.text),
        urgency=classify_urgency(message.text),
    )
    await core._finish(message.message_id, created_message(language, ticket), message.sender)


def _privacy_delete(store: Any, phone: str) -> bool:
    support_deleted = _repository(store).delete_user(phone)
    return bool(_ORIGINAL_PRIVACY_DELETE(store, phone) or support_deleted)


def _privacy_cleanup(store: Any, **kwargs: Any) -> dict[str, int]:
    result = _ORIGINAL_PRIVACY_CLEANUP(store, **kwargs)
    result["support_tickets"] = _repository(store).cleanup(
        now=kwargs.get("now"),
        resolved_days=int(os.getenv("SUPPORT_RESOLVED_RETENTION_DAYS", "90")),
        cancelled_days=int(os.getenv("SUPPORT_CANCELLED_RETENTION_DAYS", "30")),
    )
    return result


def _build_overview(store: Any, **kwargs: Any) -> dict[str, Any]:
    payload = _ORIGINAL_ADMIN_BUILD_OVERVIEW(store, **kwargs)
    payload["human_support"] = _repository(store).aggregate()
    return payload


def _provided_support_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "").strip()
    if authorization.casefold().startswith("bearer "):
        return authorization[7:].strip()
    return request.headers.get("x-support-token", "").strip()


def _support_authorize(request: Request) -> JSONResponse | None:
    configured = os.getenv("SUPPORT_API_TOKEN", "").strip()
    if not support_configured() or not configured:
        return JSONResponse({"status": "not_found"}, status_code=404, headers={"Cache-Control": "no-store"})
    supplied = _provided_support_token(request)
    if not supplied or not hmac.compare_digest(configured, supplied):
        return JSONResponse(
            {"status": "unauthorized"},
            status_code=401,
            headers={"Cache-Control": "no-store", "WWW-Authenticate": "Bearer"},
        )
    return None


@core.app.get("/admin/support/tickets", include_in_schema=False)
async def list_support_tickets(request: Request) -> JSONResponse:
    denied = _support_authorize(request)
    if denied is not None:
        return denied
    status = request.query_params.get("status", "open")
    try:
        limit = int(request.query_params.get("limit", "50"))
    except ValueError:
        limit = 50
    repository = _repository()
    tickets = repository.list_admin(status=status, limit=limit)
    repository.record_admin_event("tickets_listed")
    return JSONResponse(
        {
            "status": "ok",
            "purpose": "human_support_contact_queue",
            "tickets": tickets,
        },
        headers={"Cache-Control": "no-store"},
    )


@core.app.post("/admin/support/tickets/{ticket_id}/status", include_in_schema=False)
async def update_support_ticket(ticket_id: str, request: Request) -> JSONResponse:
    denied = _support_authorize(request)
    if denied is not None:
        return denied
    try:
        payload = await request.json()
    except ValueError:
        payload = {}
    status = str(payload.get("status") or "") if isinstance(payload, dict) else ""
    if status not in {"assigned", "resolved", "cancelled"}:
        return JSONResponse(
            {"status": "invalid_request"},
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )
    repository = _repository()
    ticket = repository.update_admin_status(ticket_id, status)
    if ticket is None:
        return JSONResponse(
            {"status": "not_found"},
            status_code=404,
            headers={"Cache-Control": "no-store"},
        )
    repository.record_admin_event(f"ticket_{status}")
    return JSONResponse(
        {"status": "ok", "ticket": ticket},
        headers={"Cache-Control": "no-store"},
    )


_repository()
privacy_composed.delete_all_user_data = _privacy_delete
privacy_module.cleanup_retention = _privacy_cleanup
admin_module.build_overview = _build_overview
core.process_incoming = process_incoming

app = composed.app
store = composed.store
SUPPORT_STATUS = "configured" if support_configured() else ("misconfigured" if support_enabled() else "disabled")
