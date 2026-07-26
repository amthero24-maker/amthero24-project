"""Safe lease release for interrupted durable inbound work.

The helper updates operational queue state only. It does not read or expose encrypted
sender/media envelopes, message text, user profiles, or provider payloads.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    return current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)


def release_processing_item(
    store: Any,
    message_id: str,
    *,
    delay_seconds: int = 30,
    code: str = "shutdown_interrupted",
    now: datetime | None = None,
) -> bool:
    """Return one still-processing item to the queue after a bounded delay."""
    if str(getattr(store, "backend_name", "json")) != "postgresql":
        return False
    clean_id = str(message_id or "").strip()
    if not clean_id:
        return False
    current = _now(now)
    delay = min(max(int(delay_seconds), 5), 300)
    safe_code = "".join(
        character for character in str(code or "shutdown_interrupted")
        if character.isalnum() or character in {"_", "-"}
    )[:80] or "shutdown_interrupted"
    with store.pool.connection() as connection:
        result = connection.execute(
            """
            UPDATE inbound_work_queue
            SET status = 'queued', available_at = %s, lease_until = NULL,
                last_failure_code = %s, updated_at = %s
            WHERE message_id = %s AND status = 'processing'
            """,
            (current + timedelta(seconds=delay), safe_code, current, clean_id),
        )
    return bool(result.rowcount and result.rowcount > 0)
