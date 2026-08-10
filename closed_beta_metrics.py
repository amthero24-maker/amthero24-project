"""Aggregate-only operational visibility for Closed Beta admission.

No function in this module returns tenant keys, recipient hashes, phone numbers,
message content, or document content. Configuration and storage failures are
reported with bounded states and never echo raw values.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from closed_beta_onboarding import onboarding_config

_FORBIDDEN_IDENTIFIER_KEYS = {
    "tenant_key",
    "phone",
    "phone_hash",
    "sender",
    "recipient",
    "recipient_ciphertext",
    "user_id",
    "message",
    "message_text",
    "raw_text",
    "document",
    "document_content",
}


def _normalized_environment(environment: Mapping[str, str]) -> dict[str, str]:
    normalized = {str(key): str(value) for key, value in environment.items()}
    if not normalized.get("CLOSED_BETA_TENANT_KEY", "").strip():
        normalized["CLOSED_BETA_TENANT_KEY"] = "default"
    return normalized


def _bounded_failure(
    state: str,
    *,
    enabled: bool = False,
    capacity: int = 0,
    wave: str = "unavailable",
) -> dict[str, object]:
    return {
        "state": state,
        "enabled": enabled,
        "verified": False,
        "wave": wave,
        "capacity": max(0, int(capacity)),
        "admitted_count": 0,
        "remaining_slots": 0,
        "full": True,
        "over_capacity": False,
    }


def _json_active_count(store: Any, tenant_key: str, wave: str) -> int:
    snapshot = store.snapshot()
    records = (
        snapshot.get("closed_beta_admissions", {})
        .get(tenant_key, {})
        .get(wave, {})
    )
    if not isinstance(records, dict):
        return 0
    return sum(
        1
        for record in records.values()
        if isinstance(record, dict) and record.get("status") == "active"
    )


def _postgres_active_count(store: Any, tenant_key: str, wave: str) -> int:
    with store.pool.connection() as connection:
        table_row = connection.execute(
            "SELECT to_regclass('closed_beta_admissions') AS table_name"
        ).fetchone()
        table_name = (
            table_row.get("table_name")
            if hasattr(table_row, "get")
            else table_row[0] if table_row else None
        )
        if not table_name:
            raise RuntimeError("closed_beta_schema_unavailable")
        row = connection.execute(
            """
            SELECT COUNT(*) AS active_count
            FROM closed_beta_admissions
            WHERE tenant_key = %s AND wave = %s AND status = 'active'
            """,
            (tenant_key, wave),
        ).fetchone()
    value = row.get("active_count") if hasattr(row, "get") else row[0] if row else 0
    return max(0, int(value or 0))


def build_closed_beta_metrics(
    store: Any,
    *,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Return verified aggregate capacity state for the configured Beta wave."""
    environment = os.environ if env is None else env
    try:
        config = onboarding_config(_normalized_environment(environment))
    except (TypeError, ValueError):
        return _bounded_failure("misconfigured")

    backend = str(getattr(store, "backend_name", "unknown"))
    try:
        if backend == "postgresql":
            admitted_count = _postgres_active_count(
                store,
                config.tenant_key,
                config.wave,
            )
        elif backend == "json":
            admitted_count = _json_active_count(
                store,
                config.tenant_key,
                config.wave,
            )
        else:
            return _bounded_failure(
                "unavailable",
                enabled=config.enabled,
                capacity=config.capacity,
                wave=config.wave,
            )
    except Exception:
        return _bounded_failure(
            "unavailable",
            enabled=config.enabled,
            capacity=config.capacity,
            wave=config.wave,
        )

    over_capacity = admitted_count > config.capacity
    full = admitted_count >= config.capacity
    remaining_slots = max(config.capacity - admitted_count, 0)
    if over_capacity:
        state = "over_capacity"
    elif not config.enabled:
        state = "disabled"
    elif full:
        state = "full"
    else:
        state = "open"
    return {
        "state": state,
        "enabled": config.enabled,
        "verified": True,
        "wave": config.wave,
        "capacity": config.capacity,
        "admitted_count": admitted_count,
        "remaining_slots": remaining_slots,
        "full": full,
        "over_capacity": over_capacity,
    }


def closed_beta_launch_check(metrics: Mapping[str, object] | None) -> dict[str, object]:
    """Translate aggregate admission state into a bounded launch-readiness check."""
    payload = metrics if isinstance(metrics, Mapping) else {}
    state = str(payload.get("state") or "unavailable")
    verified = bool(payload.get("verified"))
    try:
        capacity = max(0, int(payload.get("capacity", 0) or 0))
        admitted = max(0, int(payload.get("admitted_count", 0) or 0))
    except (TypeError, ValueError):
        state = "misconfigured"
        verified = False
        capacity = 0
        admitted = 0

    if not verified or state in {"misconfigured", "unavailable"}:
        detail = (
            "Closed Beta admission configuration is invalid."
            if state == "misconfigured"
            else "Closed Beta admission capacity cannot be verified."
        )
        return {
            "code": "closed_beta_admission",
            "status": "blocked",
            "detail": detail,
            "action": "Keep admission disabled and restore verified configuration and PostgreSQL state before admitting users.",
        }
    if state == "over_capacity" or admitted > capacity:
        return {
            "code": "closed_beta_admission",
            "status": "blocked",
            "detail": f"Closed Beta active admissions ({admitted}) exceed configured capacity ({capacity}).",
            "action": "Stop new admissions and reconcile aggregate admission state before expansion.",
        }
    if state == "disabled":
        return {
            "code": "closed_beta_admission",
            "status": "ready",
            "detail": f"Closed Beta admission is safely disabled; {admitted} active admission record(s) remain monitored.",
        }
    if state == "full":
        return {
            "code": "closed_beta_admission",
            "status": "ready",
            "detail": f"Closed Beta capacity is full at {admitted}/{capacity}; new admissions stop automatically.",
        }
    if state == "open":
        return {
            "code": "closed_beta_admission",
            "status": "ready",
            "detail": f"Closed Beta admission is open at {admitted}/{capacity} active slots.",
        }
    return {
        "code": "closed_beta_admission",
        "status": "blocked",
        "detail": "Closed Beta admission state is not recognized.",
        "action": "Keep admission disabled and verify the bounded admission state before admitting users.",
    }


def apply_closed_beta_launch_check(
    report: Mapping[str, object],
    metrics: Mapping[str, object] | None,
) -> dict[str, object]:
    """Add one admission check and recompute aggregate launch status."""
    checks = [
        dict(item)
        for item in report.get("checks", [])
        if isinstance(item, dict) and item.get("code") != "closed_beta_admission"
    ]
    checks.append(closed_beta_launch_check(metrics))
    statuses = [str(item.get("status") or "blocked") for item in checks]
    payload = dict(report)
    payload["checks"] = checks
    payload["status"] = (
        "blocked"
        if "blocked" in statuses
        else "warning" if "warning" in statuses else "ready"
    )
    payload["summary"] = {
        status: statuses.count(status)
        for status in ("ready", "warning", "blocked")
    }
    payload["next_actions"] = [
        str(item["action"])
        for item in checks
        if str(item.get("action") or "").strip()
    ]
    return payload


def contains_closed_beta_identifiers(payload: Any) -> bool:
    """Defense-in-depth guard for admission metrics and launch evidence."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).casefold() in _FORBIDDEN_IDENTIFIER_KEYS:
                return True
            if contains_closed_beta_identifiers(value):
                return True
        return False
    if isinstance(payload, (list, tuple)):
        return any(contains_closed_beta_identifiers(item) for item in payload)
    return False
