"""Controlled-Beta launch policy for encrypted backup freshness."""
from __future__ import annotations

import os
from copy import deepcopy
from typing import Any, Mapping

from database_migrations import LATEST_SCHEMA_VERSION


def _flag(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return str(env.get(name, fallback)).strip().casefold() in {"1", "true", "yes", "on"}


def _hours(env: Mapping[str, str], name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(env.get(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


def backup_thresholds(environment: Mapping[str, str] | None = None) -> tuple[int, int]:
    env = environment or os.environ
    warning = _hours(env, "BACKUP_WARNING_AFTER_HOURS", 30, 1, 168)
    blocked = _hours(env, "BACKUP_BLOCK_AFTER_HOURS", 48, warning + 1, 336)
    return warning, blocked


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, parsed)


def backup_freshness_check(
    overview: dict[str, Any],
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = environment or os.environ
    enforced = _flag(env, "BACKUP_FRESHNESS_ENFORCEMENT_ENABLED", False)
    warning_after, block_after = backup_thresholds(env)
    raw = overview.get("backups", {})
    payload = raw if isinstance(raw, dict) else {}
    state = str(payload.get("state") or "unavailable")
    last_status = str(payload.get("last_status") or "unknown")
    encrypted = bool(payload.get("encrypted"))
    schema_version = max(0, int(payload.get("schema_version") or 0))
    age_hours = _number(payload.get("age_hours"))

    def not_ready(detail: str, action: str) -> dict[str, str]:
        return {
            "code": "backup_freshness",
            "status": "blocked" if enforced else "warning",
            "detail": detail,
            "action": action,
        }

    if state == "unavailable":
        return not_ready(
            "Backup freshness state is unavailable.",
            "Verify the PostgreSQL backup checkpoint table and run one encrypted backup before expanding Beta.",
        )
    if state == "missing" or age_hours is None:
        return not_ready(
            "No verified encrypted backup success has been recorded.",
            "Run the Railway backup service, verify its persistent volume, and confirm a schema-bound manifest.",
        )
    if state == "invalid":
        return {
            "code": "backup_freshness",
            "status": "blocked",
            "detail": "The recorded backup timestamp is invalid.",
            "action": "Check clock synchronization and create a new verified encrypted backup.",
        }
    if not encrypted:
        return {
            "code": "backup_freshness",
            "status": "blocked",
            "detail": "The latest recorded backup is not marked as encrypted.",
            "action": "Configure BACKUP_ENCRYPTION_KEY and replace the artifact with an encrypted schema-bound backup.",
        }
    if schema_version != LATEST_SCHEMA_VERSION:
        return {
            "code": "backup_freshness",
            "status": "blocked",
            "detail": f"The latest backup schema version is {schema_version}; application schema version is {LATEST_SCHEMA_VERSION}.",
            "action": "Create a new backup after the current database migration completes.",
        }
    if age_hours >= block_after:
        return not_ready(
            f"The latest verified backup is {age_hours:.1f} hours old; the block threshold is {block_after} hours.",
            "Pause Beta growth and restore the scheduled backup service before accepting more users.",
        )
    if age_hours >= warning_after:
        return {
            "code": "backup_freshness",
            "status": "warning",
            "detail": f"The latest verified backup is {age_hours:.1f} hours old; the warning threshold is {warning_after} hours.",
            "action": "Check the Railway cron schedule and persistent backup volume before the block threshold is reached.",
        }
    if last_status == "failed":
        return {
            "code": "backup_freshness",
            "status": "warning",
            "detail": f"A verified backup exists from {age_hours:.1f} hours ago, but the latest backup attempt failed.",
            "action": "Inspect the safe backup failure code and repair the scheduled job before the current artifact becomes stale.",
        }
    return {
        "code": "backup_freshness",
        "status": "ready",
        "detail": f"The latest verified encrypted backup is {age_hours:.1f} hours old and matches schema version {schema_version}.",
    }


def augment_launch_report(
    report: dict[str, Any],
    overview: dict[str, Any],
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    payload = deepcopy(report)
    checks = [
        item for item in payload.get("checks", [])
        if isinstance(item, dict) and item.get("code") != "backup_freshness"
    ]
    checks.append(backup_freshness_check(overview, environment=environment))
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
