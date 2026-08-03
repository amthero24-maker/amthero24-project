"""Protected Beta launch report composed above production reliability layers."""
from __future__ import annotations

import os
import re

from fastapi import Request
from fastapi.responses import JSONResponse

import admin_extensions as admin_module
import provider_extensions as composed
from config import APP_VERSION, GROQ_MODEL
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


def _apply_controlled_canary_scope(report: dict[str, object]) -> dict[str, object]:
    """Treat intentionally disabled reminder execution as safe for read-only Canary."""
    if _flag("REMINDER_WORKER_ENABLED", False):
        return report
    checks = report.get("checks")
    if not isinstance(checks, list):
        return report
    scoped_checks: list[dict[str, object]] = []
    for raw in checks:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        code = str(item.get("code") or "")
        if code == "reminder_encryption":
            item = {
                "code": code,
                "status": "ready",
                "detail": "Reminder creation and delivery are disabled for the read-only Controlled Canary.",
            }
        elif code == "reminder_delivery":
            item = {
                "code": code,
                "status": "ready",
                "detail": "Reminder delivery is outside the current Controlled Canary scope.",
            }
        scoped_checks.append(item)
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


@core.app.get("/admin/launch-readiness", include_in_schema=False)
async def launch_readiness(request: Request) -> JSONResponse:
    denied = admin_module._authorize(request)
    if denied is not None:
        return denied
    try:
        overview = admin_module.build_overview(core.store, version=APP_VERSION, model=GROQ_MODEL)
    except Exception as exc:
        return _unavailable(_overview_failure_code(exc))
    try:
        report = _apply_controlled_canary_scope(build_launch_report(overview))
    except Exception:
        return _unavailable("launch_report_build_failed")
    if admin_module.contains_personal_fields(report):
        return _unavailable("personal_field_guard_failed")
    return JSONResponse(report, headers={"Cache-Control": "no-store"})


@core.app.get("/admin/reminder-encryption-preflight", include_in_schema=False)
async def reminder_encryption_preflight(request: Request) -> JSONResponse:
    """Classify reminder ciphertexts without changing rows or exposing identifiers."""
    denied = admin_module._authorize(request)
    if denied is not None:
        return denied
    try:
        report = migrate_reminder_ciphertexts(
            os.getenv("DATABASE_URL", ""),
            new_key=os.getenv("REMINDER_ENCRYPTION_KEY", ""),
            legacy_token=os.getenv("WHATSAPP_TOKEN", ""),
            apply=False,
        ).as_dict()
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
    if admin_module.contains_personal_fields(payload):
        return _unavailable("personal_field_guard_failed")
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


app = composed.app
store = composed.store
