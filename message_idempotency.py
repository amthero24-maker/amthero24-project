"""Retry-safe, cross-replica claims for inbound WhatsApp messages.

Only a one-way sender hash, bounded message text, type, media-presence flag, lifecycle
state, lease, attempts, and timestamps are stored. The raw phone number and media ID are
never persisted here.
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

_MESSAGE_LEASE = timedelta(minutes=10)
_TERMINAL_STATUS = "sent"
_RETRYABLE_STATUS = "failed"
_ACTIVE_STATUSES = {"processing", "claimed"}


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    return current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)


def _as_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _phone_hash(phone: str) -> str:
    normalized = "".join(character for character in str(phone or "") if character.isdigit() or character == "+")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class MessageClaimRepository:
    """Atomically claim new, failed, or abandoned inbound messages."""

    def __init__(self, store: Any, *, lease: timedelta = _MESSAGE_LEASE) -> None:
        self.store = store
        self.backend_name = str(getattr(store, "backend_name", "json"))
        self.lease = max(lease, timedelta(minutes=1))
        if self.backend_name == "postgresql":
            self._initialize_postgres_schema()

    def _initialize_postgres_schema(self) -> None:
        statements = (
            "ALTER TABLE inbound_messages ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ",
            "ALTER TABLE inbound_messages ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0",
            """
            CREATE INDEX IF NOT EXISTS inbound_messages_status_lease_idx
            ON inbound_messages (status, lease_until, updated_at)
            """,
        )
        with self.store.pool.connection() as connection:
            for statement in statements:
                connection.execute(statement)

    def claim(
        self,
        message_id: str,
        phone: str,
        text: str = "",
        *,
        message_type: str = "text",
        media_id: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Return true only for the worker that owns the current processing lease."""
        clean_id = str(message_id or "").strip()
        clean_phone = str(phone or "").strip()
        if not clean_id or not clean_phone:
            return False

        current = _now(now)
        lease_until = current + self.lease
        key = _phone_hash(clean_phone)
        clean_text = str(text or "")[:2000]
        clean_type = str(message_type or "text")[:40]
        has_media = bool(media_id)

        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                inserted = connection.execute(
                    """
                    INSERT INTO inbound_messages
                        (message_id, phone_hash, text, message_type, has_media, status,
                         lease_until, attempt_count, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, 'processing', %s, 1, %s, %s)
                    ON CONFLICT (message_id) DO NOTHING
                    RETURNING message_id
                    """,
                    (clean_id, key, clean_text, clean_type, has_media, lease_until, current, current),
                ).fetchone()
                if inserted:
                    return True

                stale_cutoff = current - self.lease
                reclaimed = connection.execute(
                    """
                    UPDATE inbound_messages
                    SET status = 'processing', lease_until = %s,
                        attempt_count = attempt_count + 1, updated_at = %s
                    WHERE message_id = %s
                      AND phone_hash = %s
                      AND (
                            status = 'failed'
                         OR (status = 'processing' AND (lease_until IS NULL OR lease_until <= %s))
                         OR (status = 'claimed' AND updated_at <= %s)
                      )
                    RETURNING message_id
                    """,
                    (lease_until, current, clean_id, key, current, stale_cutoff),
                ).fetchone()
            return reclaimed is not None

        def claim_json(data: dict[str, Any]) -> bool:
            messages = data.setdefault("messages", {})
            record = messages.get(clean_id)
            if not isinstance(record, dict):
                messages[clean_id] = {
                    "phone_hash": key,
                    "text": clean_text,
                    "type": clean_type,
                    "has_media": has_media,
                    "status": "processing",
                    "lease_until": lease_until.isoformat(),
                    "attempt_count": 1,
                    "created_at": current.isoformat(),
                    "updated_at": current.isoformat(),
                }
                return True

            if record.get("phone_hash") != key or record.get("status") == _TERMINAL_STATUS:
                return False

            status = str(record.get("status") or "")
            existing_lease = _as_datetime(record.get("lease_until"))
            updated_at = _as_datetime(record.get("updated_at"))
            stale_legacy = status == "claimed" and (
                updated_at is None or updated_at <= current - self.lease
            )
            stale_processing = status == "processing" and (
                existing_lease is None or existing_lease <= current
            )
            if status != _RETRYABLE_STATUS and not stale_legacy and not stale_processing:
                return False

            record["status"] = "processing"
            record["lease_until"] = lease_until.isoformat()
            record["attempt_count"] = int(record.get("attempt_count") or 0) + 1
            record["updated_at"] = current.isoformat()
            return True

        return bool(self.store._transaction(claim_json))

    def state(self, message_id: str) -> dict[str, Any] | None:
        """Return non-sensitive lifecycle metadata for diagnostics and tests."""
        clean_id = str(message_id or "").strip()
        if not clean_id:
            return None
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                row = connection.execute(
                    """
                    SELECT status, attempt_count, lease_until, created_at, updated_at
                    FROM inbound_messages WHERE message_id = %s
                    """,
                    (clean_id,),
                ).fetchone()
            if not row:
                return None
            payload = dict(row)
            for field in ("lease_until", "created_at", "updated_at"):
                value = payload.get(field)
                if isinstance(value, datetime):
                    payload[field] = value.astimezone(UTC).isoformat()
            return payload

        record = self.store.snapshot().get("messages", {}).get(clean_id)
        if not isinstance(record, dict):
            return None
        allowed = {"status", "attempt_count", "lease_until", "created_at", "updated_at"}
        return {key: deepcopy(record[key]) for key in allowed if key in record}
