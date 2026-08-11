"""Read-only aggregate outbound-delivery observability helpers."""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

_RECOVERY_EVIDENCE = {"none", "success_only", "success_after_failure", "unresolved_failure"}


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    return current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)


def _integer(value: Any) -> int:
    if isinstance(value, Decimal):
        return int(value)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_failure_code(value: Any) -> str:
    """Return a bounded provider code only; never reflect provider error text."""
    raw = str(value or "").strip()
    if not raw:
        return "unknown"
    clean = "".join(character for character in raw if character.isalnum() or character in {"_", "-"})[:40]
    return clean or "unknown"


def _failure_code_dict(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted((code, int(count)) for code, count in counter.items()))


def _recovery_payload(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    required = raw.get("recovery_required") is True
    evidence = str(raw.get("recovery_evidence") or "none")
    if evidence not in _RECOVERY_EVIDENCE:
        evidence = "unresolved_failure" if required else "none"
    return {
        "recovery_required": required,
        "recovery_evidence": evidence,
        "recovery_failure_code": (
            _safe_failure_code(raw.get("recovery_failure_code")) if required else ""
        ),
    }


def build_outbound_delivery_overview(
    store: Any,
    *,
    now: datetime | None = None,
    recovery: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return aggregate delivery health without exposing identifiers or message content."""
    current = _now(now)
    cutoff = current - timedelta(hours=24)
    pending_cutoff = current - timedelta(minutes=15)
    counts = {status: 0 for status in ("accepted", "sent", "delivered", "read", "failed")}
    failure_codes: Counter[str] = Counter()

    if str(getattr(store, "backend_name", "json")) != "postgresql":
        records = store.snapshot().get("outbound_delivery", {}).values()
        pending = 0
        oldest = 0
        for record in records:
            if not isinstance(record, dict):
                continue
            accepted_at = record.get("accepted_at")
            expires_at = record.get("expires_at")
            try:
                accepted = datetime.fromisoformat(str(accepted_at))
                accepted = accepted.replace(tzinfo=UTC) if accepted.tzinfo is None else accepted.astimezone(UTC)
            except (TypeError, ValueError):
                continue
            try:
                expires = datetime.fromisoformat(str(expires_at)) if expires_at else None
                if expires is not None:
                    expires = expires.replace(tzinfo=UTC) if expires.tzinfo is None else expires.astimezone(UTC)
            except (TypeError, ValueError):
                expires = None
            if expires and expires <= current:
                continue
            status = str(record.get("status") or "")
            if accepted >= cutoff and status in counts:
                counts[status] += 1
                if status == "failed":
                    failure_codes[_safe_failure_code(record.get("failure_code"))] += 1
            if status in {"accepted", "sent"}:
                age = max(0, int((current - accepted).total_seconds()))
                oldest = max(oldest, age)
                if accepted <= pending_cutoff:
                    pending += 1
    else:
        with store.pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM outbound_delivery_messages
                WHERE accepted_at >= %s AND expires_at > %s
                GROUP BY status
                """,
                (cutoff, current),
            ).fetchall()
            failure_rows = connection.execute(
                """
                SELECT failure_code, COUNT(*) AS count
                FROM outbound_delivery_messages
                WHERE accepted_at >= %s
                  AND expires_at > %s
                  AND status = 'failed'
                GROUP BY failure_code
                """,
                (cutoff, current),
            ).fetchall()
            aggregate = connection.execute(
                """
                WITH delivery_aggregate AS (
                    SELECT
                        COUNT(*) FILTER (
                            WHERE status IN ('accepted', 'sent')
                              AND accepted_at <= %s AND expires_at > %s
                        ) AS pending_over_15m,
                        MIN(accepted_at) FILTER (
                            WHERE status IN ('accepted', 'sent') AND expires_at > %s
                        ) AS oldest_pending_at
                    FROM outbound_delivery_messages
                )
                SELECT
                    pending_over_15m,
                    COALESCE(EXTRACT(EPOCH FROM (%s - oldest_pending_at)), 0)
                        AS oldest_pending_age_seconds
                FROM delivery_aggregate
                """,
                (pending_cutoff, current, current, current),
            ).fetchone()
        for row in rows:
            status = str(row.get("status") or "")
            if status in counts:
                counts[status] = _integer(row.get("count"))
        for row in failure_rows:
            failure_codes[_safe_failure_code(row.get("failure_code"))] += _integer(row.get("count"))
        pending = _integer(aggregate.get("pending_over_15m") if aggregate else 0)
        oldest = max(0, _integer(aggregate.get("oldest_pending_age_seconds") if aggregate else 0))

    tracked = sum(counts.values())
    successes = counts["delivered"] + counts["read"]
    terminal = successes + counts["failed"]
    success_rate = round((successes / terminal) * 100, 1) if terminal else 0.0
    return {
        "tracked_24h": tracked,
        "by_status": counts,
        "failure_codes": _failure_code_dict(failure_codes),
        "terminal_24h": terminal,
        "delivery_success_pct": success_rate,
        "pending_over_15m": pending,
        "oldest_pending_age_seconds": oldest,
        **_recovery_payload(recovery),
    }
