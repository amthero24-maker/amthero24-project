"""Protected Beta launch report composed above production reliability layers."""
from __future__ import annotations

import os
import re
from typing import Any

from fastapi import Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

import admin_extensions as admin_module
import provider_extensions as composed
from closed_beta_metrics import (
    apply_closed_beta_launch_check,
    build_closed_beta_metrics,
    contains_closed_beta_identifiers,
)
from config import APP_VERSION, GROQ_MODEL
from encryption_policy import assess_secret
from launch_readiness import build_launch_report
from scripts.migrate_reminder_encryption import migrate_reminder_ciphertexts

core = composed.core


def _unavailable(code: str) -> JSONResponse:
    """Return a bounded stage code without exposing exception or production data."""
    return JSONResponse(
        {"status": code},
        status_code=500,
        headers={"Cache-Control": "no-store"},
    )


def _overview_failure_code(exc: Exception) -> str:
    """Return only a bounded exception class identifier; never reflect its message."""
    name = re.sub(r"[^a-z0-9]", "", type(exc).__name__.casefold())[:40]
    return f"overview_exception_{name or 'unknown'}"


def _flag(name: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).strip().casefold() in {"1", "true", "yes", "on"}


def _queue_check(queue_enabled: bool) -> dict[str, object]:
    if not queue_enabled:
        return {
            "code": "durable_queue",
            "status": "ready",
            "detail": "Durable recovery is outside the single-sender read-only Canary; immediate webhook idempotency remains active.",
        }
    key_status = assess_secret("MESSAGE_QUEUE_ENCRYPTION_KEY").status
    if key_status == "configured":
        return {
            "code": "durable_queue",
            "status": "ready",
            "detail": "Durable inbound recovery is enabled with a dedicated strong encryption key.",
        }
    return {
        "code": "durable_queue",
        "status": "blocked",
        "detail": f"Durable inbound recovery encryption key is {key_status}.",
        "action": "Set MESSAGE_QUEUE_ENCRYPTION_KEY to a unique random value of at least 32 characters before enabling the queue.",
    }


def _apply_controlled_canary_scope(report: dict[str, object]) -> dict[str, object]:
    """Apply only the explicitly limited Controlled Canary launch scope."""
    reminders_enabled = _flag("REMINDER_WORKER_ENABLED", False)
    reminder_canary = bool(os.getenv("REMINDER_CANARY_SENDERS", "").strip())
    reminder_template = bool(os.getenv("WHATSAPP_REMINDER_TEMPLATE", "").strip())
    queue_enabled = _flag("DURABLE_QUEUE_ENABLED", False)
    checks = report.get("checks")
    if not isinstance(checks, list):
        return report
    scoped_checks: list[dict[str, object]] = []
    queue_seen = False
    for raw in checks:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        code = str(item.get("code") or "")
        if not reminders_enabled and code == "reminder_encryption":
            item = {
                "code": code,
                "status": "ready",
                "detail": "Reminder creation and delivery are disabled for the read-only Controlled Canary.",
            }
        elif not reminders_enabled and code == "reminder_delivery":
            item = {
                "code": code,
                "status": "ready",
                "detail": "Reminder delivery is outside the current Controlled Canary scope.",
            }
        elif (
            reminders_enabled
            and reminder_canary
            and not reminder_template
            and code == "reminder_delivery"
            and item.get("status") == "warning"
        ):
            item = {
                "code": code,
                "status": "ready",
                "detail": "Reminder delivery is ready for the exact-sender Canary inside the 24-hour service window; long-term template rollout remains outside this scope.",
            }
        elif code == "durable_queue":
            queue_seen = True
            item = _queue_check(queue_enabled)
        scoped_checks.append(item)
    if not queue_seen:
        scoped_checks.append(_queue_check(queue_enabled))
    statuses = [str(item.get("status") or "warning") for item in scoped_checks]
    payload = dict(report)
    payload["checks"] = scoped_checks
    payload["status"] = "blocked" if "blocked" in statuses else ("warning" if "warning" in statuses else "ready")
    payload["summary"] = {status: statuses.count(status) for status in ("ready", "warning", "blocked")}
    payload["next_actions"] = [
        str(item["action"])
        for item in scoped_checks
        if str(item.get("action") or "").strip()
    ]
    return payload


def _build_launch_inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    """Collect synchronous database aggregates outside the ASGI event loop."""
    overview = admin_module.build_overview(
        core.store,
        version=APP_VERSION,
        model=GROQ_MODEL,
    )
    beta_metrics = build_closed_beta_metrics(core.store)
    overview["closed_beta_admission"] = beta_metrics
    return overview, beta_metrics


@core.app.get("/admin/launch-readiness", include_in_schema=False)
async def launch_readiness(request: Request) -> JSONResponse:
    denied = admin_module._authorize(request)
    if denied is not None:
        return denied
    try:
        overview, beta_metrics = await run_in_threadpool(_build_launch_inputs)
    except Exception as exc:
        return _unavailable(_overview_failure_code(exc))
    try:
        report = _apply_controlled_canary_scope(build_launch_report(overview))
        report = apply_closed_beta_launch_check(report, beta_metrics)
    except Exception:
        return _unavailable("launch_report_build_failed")
    if (
        admin_module.contains_personal_fields(report)
        or contains_closed_beta_identifiers(report)
    ):
        return _unavailable("personal_field_guard_failed")
    return JSONResponse(report, headers={"Cache-Control": "no-store"})


@core.app.get("/admin/reminder-encryption-preflight", include_in_schema=False)
async def reminder_encryption_preflight(request: Request) -> JSONResponse:
    """Classify reminder ciphertexts without changing rows or exposing identifiers."""
    denied = admin_module._authorize(request)
    if denied is not None:
        return denied
    try:
        migration = await run_in_threadpool(
            migrate_reminder_ciphertexts,
            os.getenv("DATABASE_URL", ""),
            new_key=os.getenv("REMINDER_ENCRYPTION_KEY", ""),
            legacy_token=os.getenv("WHATSAPP_TOKEN", ""),
            apply=False,
        )
        report = migration.as_dict()
    except Exception as exc:
        name = re.sub(r"[^a-z0-9]", "", type(exc).__name__.casefold())[:40]
        return _unavailable(f"reminder_preflight_exception_{name or 'unknown'}")

    allowed = {
        "mode",
        "total",
        "already_current",
        "decryptable_old_key",
        "decryptable_legacy_token",
        "unreadable",
        "migrated",
        "safe_to_apply",
    }
    payload = {key: report[key] for key in allowed if key in report}
    if (
        admin_module.contains_personal_fields(payload)
        or contains_closed_beta_identifiers(payload)
    ):
        return _unavailable("personal_field_guard_failed")
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


app = composed.app
store = composed.store
