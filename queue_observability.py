"""Aggregate-only operational metrics for the durable inbound queue.

No message IDs, sender hashes, ciphertext, phone numbers, message text, media identifiers,
or document content are returned. The report is safe to embed in protected admin and
launch-readiness payloads.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from durable_queue import queue_status


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


def _empty(mode: str) -> dict[str, Any]:
    return {
        "mode": mode,
        "total": 0,
        "by_status": {"queued": 0, "processing": 0, "completed": 0, "dead": 0},
        "ready": 0,
        "delayed": 0,
        "stale_processing": 0,
        "retrying": 0,
        "dead_24h": 0,
        "oldest_ready_age_seconds": 0,
        "max_attempt_count": 0,
    }


def build_queue_overview(store: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Return queue health using counts and ages only."""
    mode = queue_status(store)
    if str(getattr(store, "backend_name", "json")) != "postgresql":
        return _empty(mode)

    current = _now(now)
    with store.pool.connection() as connection:
        table = connection.execute(
            "SELECT to_regclass('inbound_work_queue') AS table_name"
        ).fetchone()
        if not table or not table.get("table_name"):
            return _empty("schema-missing" if mode == "configured" else mode)

        status_rows = connection.execute(
            "SELECT status, COUNT(*) AS count FROM inbound_work_queue GROUP BY status"
        ).fetchall()
        aggregate = connection.execute(
            """
            SELECT
                COUNT(*) FILTER (
                    WHERE status = 'queued' AND available_at <= %s AND expires_at > %s
                ) AS ready,
                COUNT(*) FILTER (
                    WHERE status = 'queued' AND available_at > %s AND expires_at > %s
                ) AS delayed,
                COUNT(*) FILTER (
                    WHERE status = 'processing'
                      AND (lease_until IS NULL OR lease_until <= %s)
                      AND expires_at > %s
                ) AS stale_processing,
                COUNT(*) FILTER (
                    WHERE status IN ('queued', 'processing') AND attempt_count > 1
                ) AS retrying,
                COUNT(*) FILTER (
                    WHERE status = 'dead' AND updated_at >= %s
                ) AS dead_24h,
                COALESCE(MAX(attempt_count), 0) AS max_attempt_count,
                COALESCE(
                    EXTRACT(EPOCH FROM (
                        %s - MIN(
                            CASE
                                WHEN status = 'queued' AND available_at <= %s AND expires_at > %s
                                    THEN available_at
                                WHEN status = 'processing'
                                     AND (lease_until IS NULL OR lease_until <= %s)
                                     AND expires_at > %s
                                    THEN COALESCE(lease_until, updated_at)
                                ELSE NULL
                            END
                        )
                    )),
                    0
                ) AS oldest_ready_age_seconds
            FROM inbound_work_queue
            """,
            (
                current,
                current,
                current,
                current,
                current,
                current,
                current - timedelta(hours=24),
                current,
                current,
                current,
                current,
                current,
            ),
        ).fetchone()

    statuses = {"queued": 0, "processing": 0, "completed": 0, "dead": 0}
    for row in status_rows:
        status = str(row.get("status") or "")
        if status in statuses:
            statuses[status] = _integer(row.get("count"))

    payload = _empty(mode)
    payload.update(
        {
            "total": sum(statuses.values()),
            "by_status": statuses,
            "ready": _integer(aggregate.get("ready") if aggregate else 0),
            "delayed": _integer(aggregate.get("delayed") if aggregate else 0),
            "stale_processing": _integer(aggregate.get("stale_processing") if aggregate else 0),
            "retrying": _integer(aggregate.get("retrying") if aggregate else 0),
            "dead_24h": _integer(aggregate.get("dead_24h") if aggregate else 0),
            "oldest_ready_age_seconds": max(
                0,
                _integer(aggregate.get("oldest_ready_age_seconds") if aggregate else 0),
            ),
            "max_attempt_count": _integer(aggregate.get("max_attempt_count") if aggregate else 0),
        }
    )
    return payload
