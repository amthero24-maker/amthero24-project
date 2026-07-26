"""Controlled-Beta launch policy for aggregate durable queue health."""
from __future__ import annotations

from copy import deepcopy
from typing import Any


def _number(payload: dict[str, Any], key: str) -> int:
    try:
        return max(0, int(payload.get(key, 0) or 0))
    except (TypeError, ValueError):
        return 0


def queue_launch_check(overview: dict[str, Any]) -> dict[str, str]:
    queue = overview.get("durable_queue", {})
    payload = queue if isinstance(queue, dict) else {}
    mode = str(payload.get("mode") or "unknown")
    dead = _number(payload, "dead_24h")
    stale = _number(payload, "stale_processing")
    ready = _number(payload, "ready")
    oldest = _number(payload, "oldest_ready_age_seconds")

    if mode in {"misconfigured", "requires-postgresql", "schema-missing", "unknown"}:
        return {
            "code": "durable_queue",
            "status": "blocked",
            "detail": f"Durable inbound queue is {mode}.",
            "action": "Restore PostgreSQL and configure the dedicated queue encryption key before enabling durable delivery.",
        }
    if mode == "disabled":
        return {
            "code": "durable_queue",
            "status": "warning",
            "detail": "Durable inbound recovery is disabled; immediate webhook idempotency remains active.",
            "action": "Configure MESSAGE_QUEUE_ENCRYPTION_KEY, enable DURABLE_QUEUE_ENABLED, restart, and verify /ready before expanding Beta.",
        }
    if dead >= 5:
        return {
            "code": "durable_queue",
            "status": "blocked",
            "detail": f"{dead} durable queue item(s) entered dead-letter state during the last 24 hours.",
            "action": "Pause Beta growth and investigate aggregate processing failures before accepting more traffic.",
        }
    if oldest >= 1800:
        return {
            "code": "durable_queue",
            "status": "blocked",
            "detail": f"The oldest recoverable queue item has waited {oldest} seconds.",
            "action": "Verify queue workers, PostgreSQL locks, Groq, and WhatsApp delivery before continuing Beta.",
        }
    if stale >= 5:
        return {
            "code": "durable_queue",
            "status": "blocked",
            "detail": f"{stale} processing lease(s) are stale.",
            "action": "Check worker restarts and database health; confirm expired leases are being reclaimed.",
        }
    if dead > 0 or stale > 0 or oldest >= 300 or ready >= 100:
        details: list[str] = []
        if dead:
            details.append(f"dead-letter 24h: {dead}")
        if stale:
            details.append(f"stale leases: {stale}")
        if oldest >= 300:
            details.append(f"oldest ready age: {oldest}s")
        if ready >= 100:
            details.append(f"ready backlog: {ready}")
        return {
            "code": "durable_queue",
            "status": "warning",
            "detail": "Durable queue needs operator review (" + ", ".join(details) + ").",
            "action": "Review aggregate queue metrics and keep the Beta cohort fixed until the backlog clears.",
        }
    return {
        "code": "durable_queue",
        "status": "ready",
        "detail": "Durable inbound queue has no material backlog, stale lease, or recent dead-letter signal.",
    }


def augment_launch_report(report: dict[str, Any], overview: dict[str, Any]) -> dict[str, Any]:
    """Append one aggregate queue check and recompute the launch decision."""
    payload = deepcopy(report)
    checks = [item for item in payload.get("checks", []) if isinstance(item, dict) and item.get("code") != "durable_queue"]
    check = queue_launch_check(overview)
    checks.append(check)
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
