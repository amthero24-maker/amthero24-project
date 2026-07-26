"""Controlled-Beta launch policy for aggregate WhatsApp delivery receipts."""
from __future__ import annotations

from copy import deepcopy
from typing import Any


def _integer(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return max(0.0, min(100.0, float(value or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def outbound_delivery_check(overview: dict[str, Any]) -> dict[str, str]:
    raw = overview.get("outbound_delivery", {})
    payload = raw if isinstance(raw, dict) else {}
    statuses = payload.get("by_status", {})
    counts = statuses if isinstance(statuses, dict) else {}
    tracked = _integer(payload.get("tracked_24h"))
    terminal = _integer(payload.get("terminal_24h"))
    failed = _integer(counts.get("failed"))
    pending = _integer(payload.get("pending_over_15m"))
    oldest = _integer(payload.get("oldest_pending_age_seconds"))
    success_rate = _float(payload.get("delivery_success_pct"))

    if pending >= 20 or oldest >= 3600:
        return {
            "code": "outbound_delivery",
            "status": "blocked",
            "detail": f"WhatsApp delivery receipts show {pending} message(s) pending over 15 minutes; oldest pending age is {oldest} seconds.",
            "action": "Pause Beta growth and verify WhatsApp account status, templates, token validity, and Meta delivery incidents.",
        }
    if terminal >= 10 and failed >= 10 and success_rate < 80.0:
        return {
            "code": "outbound_delivery",
            "status": "blocked",
            "detail": f"WhatsApp terminal delivery success is {success_rate:.1f}% with {failed} current failure(s) during the last 24 hours.",
            "action": "Investigate aggregate Meta failure codes and account/template health before accepting more Beta traffic.",
        }
    if pending > 0 or oldest >= 900 or (failed > 0 and terminal > 0 and success_rate < 95.0):
        details: list[str] = []
        if pending:
            details.append(f"pending over 15m: {pending}")
        if oldest >= 900:
            details.append(f"oldest pending: {oldest}s")
        if failed:
            details.append(f"failures: {failed}")
            details.append(f"terminal success: {success_rate:.1f}%")
        return {
            "code": "outbound_delivery",
            "status": "warning",
            "detail": "WhatsApp delivery receipts need operator review (" + ", ".join(details) + ").",
            "action": "Keep the Beta cohort fixed while checking aggregate delivery metrics and Meta service health.",
        }
    if tracked == 0:
        return {
            "code": "outbound_delivery",
            "status": "ready",
            "detail": "Outbound delivery tracking is initialized; no messages were tracked during the last 24 hours.",
        }
    return {
        "code": "outbound_delivery",
        "status": "ready",
        "detail": f"WhatsApp delivery receipts are healthy across {tracked} tracked message(s) during the last 24 hours.",
    }


def augment_launch_report(report: dict[str, Any], overview: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(report)
    checks = [
        item for item in payload.get("checks", [])
        if isinstance(item, dict) and item.get("code") != "outbound_delivery"
    ]
    checks.append(outbound_delivery_check(overview))
    statuses = [str(item.get("status") or "warning") for item in checks]
    payload["checks"] = checks
    payload["status"] = "blocked" if "blocked" in statuses else ("warning" if "warning" in statuses else "ready")
    payload["summary"] = {status: statuses.count(status) for status in ("ready", "warning", "blocked")}
    payload["next_actions"] = [
        str(item["action"])
        for item in checks
        if str(item.get("action") or "").strip()
    ]
    return payload
