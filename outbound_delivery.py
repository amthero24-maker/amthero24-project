"""Privacy-safe WhatsApp outbound delivery tracking.

Meta message identifiers are hashed immediately. The repository never stores recipient
phones, sender hashes, message text, templates, media identifiers, error messages, or
raw webhook payloads. Only lifecycle timestamps, a bounded message kind, and a generic
failure code are retained for aggregate reliability operations.
"""
from __future__ import annotations

import hashlib
import os
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable

_ALLOWED_STATUSES = {"sent", "delivered", "read", "failed"}
_SUCCESS_FIELDS = {
    "sent": "sent_at",
    "delivered": "delivered_at",
    "read": "read_at",
}


@dataclass(frozen=True)
class DeliveryReceipt:
    message_id: str
    status: str
    occurred_at: datetime
    failure_code: str = ""


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    return current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)


def _parse_timestamp(value: Any, *, fallback: datetime | None = None) -> datetime:
    try:
        return datetime.fromtimestamp(int(str(value)), tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return _now(fallback)


def _as_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _minimum(existing: datetime | None, candidate: datetime) -> datetime:
    return candidate if existing is None or candidate < existing else existing


def _maximum(existing: datetime | None, candidate: datetime) -> datetime:
    return candidate if existing is None or candidate > existing else existing


def _message_hash(message_id: str) -> str:
    return hashlib.sha256(str(message_id or "").encode("utf-8")).hexdigest()


def _retention() -> timedelta:
    try:
        days = int(os.getenv("OUTBOUND_DELIVERY_RETENTION_DAYS", "30").strip())
    except ValueError:
        days = 30
    return timedelta(days=min(max(days, 1), 180))


def _failure_code(status: dict[str, Any]) -> str:
    errors = status.get("errors")
    if not isinstance(errors, list) or not errors or not isinstance(errors[0], dict):
        return ""
    raw = str(errors[0].get("code") or "").strip()
    return "".join(character for character in raw if character.isalnum() or character in {"_", "-"})[:40]


def extract_delivery_receipts(payload: Any, *, now: datetime | None = None) -> list[DeliveryReceipt]:
    """Extract supported receipt fields without retaining recipient or error text."""
    if not isinstance(payload, dict):
        return []
    receipts: list[DeliveryReceipt] = []
    for entry in payload.get("entry", []):
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes", []):
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            for status in value.get("statuses", []):
                if not isinstance(status, dict):
                    continue
                message_id = str(status.get("id") or "").strip()
                state = str(status.get("status") or "").strip().casefold()
                if not message_id or state not in _ALLOWED_STATUSES:
                    continue
                receipts.append(DeliveryReceipt(
                    message_id=message_id,
                    status=state,
                    occurred_at=_parse_timestamp(status.get("timestamp"), fallback=now),
                    failure_code=_failure_code(status) if state == "failed" else "",
                ))
    return receipts


def extract_response_message_ids(response: Any) -> list[str]:
    if not isinstance(response, dict):
        return []
    identifiers: list[str] = []
    for item in response.get("messages", []):
        if not isinstance(item, dict):
            continue
        message_id = str(item.get("id") or "").strip()
        if message_id and message_id not in identifiers:
            identifiers.append(message_id)
    return identifiers


def _derive_status(record: dict[str, Any]) -> str:
    read_at = _as_datetime(record.get("read_at"))
    delivered_at = _as_datetime(record.get("delivered_at"))
    sent_at = _as_datetime(record.get("sent_at"))
    failed_at = _as_datetime(record.get("failed_at"))
    if read_at:
        return "read"
    if delivered_at:
        return "delivered"
    if failed_at and (sent_at is None or failed_at >= sent_at):
        return "failed"
    if sent_at:
        return "sent"
    if failed_at:
        return "failed"
    return "accepted"


def _serialize_time(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def _integer(value: Any) -> int:
    if isinstance(value, Decimal):
        return int(value)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class OutboundDeliveryRepository:
    """Store only hashed message lifecycle data in JSON or PostgreSQL."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.backend_name = str(getattr(store, "backend_name", "json"))
        if self.backend_name == "postgresql":
            self._initialize_postgres_schema()

    def _initialize_postgres_schema(self) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS outbound_delivery_messages (
                message_hash TEXT PRIMARY KEY,
                message_kind TEXT NOT NULL DEFAULT 'unknown',
                status TEXT NOT NULL DEFAULT 'accepted',
                accepted_at TIMESTAMPTZ NOT NULL,
                sent_at TIMESTAMPTZ,
                delivered_at TIMESTAMPTZ,
                read_at TIMESTAMPTZ,
                failed_at TIMESTAMPTZ,
                failure_code TEXT NOT NULL DEFAULT '',
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS outbound_delivery_status_idx ON outbound_delivery_messages (status, accepted_at)",
            "CREATE INDEX IF NOT EXISTS outbound_delivery_expiry_idx ON outbound_delivery_messages (expires_at)",
        )
        with self.store.pool.connection() as connection:
            for statement in statements:
                connection.execute(statement)

    def record_accepted(
        self,
        message_id: str,
        *,
        message_kind: str = "unknown",
        now: datetime | None = None,
    ) -> bool:
        clean_id = str(message_id or "").strip()
        if not clean_id:
            return False
        current = _now(now)
        key = _message_hash(clean_id)
        kind = "".join(character for character in str(message_kind or "unknown").casefold() if character.isalnum() or character in {"_", "-"})[:40] or "unknown"
        expires_at = current + _retention()
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                row = connection.execute(
                    """
                    INSERT INTO outbound_delivery_messages
                        (message_hash, message_kind, status, accepted_at,
                         expires_at, created_at, updated_at)
                    VALUES (%s, %s, 'accepted', %s, %s, %s, %s)
                    ON CONFLICT (message_hash) DO NOTHING
                    RETURNING message_hash
                    """,
                    (key, kind, current, expires_at, current, current),
                ).fetchone()
            return row is not None

        def update(data: dict[str, Any]) -> bool:
            messages = data.setdefault("outbound_delivery", {})
            if key in messages:
                return False
            messages[key] = {
                "message_kind": kind,
                "status": "accepted",
                "accepted_at": current.isoformat(),
                "sent_at": None,
                "delivered_at": None,
                "read_at": None,
                "failed_at": None,
                "failure_code": "",
                "expires_at": expires_at.isoformat(),
                "created_at": current.isoformat(),
                "updated_at": current.isoformat(),
            }
            return True

        return bool(self.store._transaction(update))

    def record_receipt(self, receipt: DeliveryReceipt, *, now: datetime | None = None) -> str:
        key = _message_hash(receipt.message_id)
        current = _now(now)
        occurred = _now(receipt.occurred_at)
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                row = connection.execute(
                    "SELECT * FROM outbound_delivery_messages WHERE message_hash = %s FOR UPDATE",
                    (key,),
                ).fetchone()
                if not row:
                    return "unknown"
                record = dict(row)
                self._apply_receipt(record, receipt.status, occurred, receipt.failure_code)
                connection.execute(
                    """
                    UPDATE outbound_delivery_messages
                    SET status = %s, sent_at = %s, delivered_at = %s, read_at = %s,
                        failed_at = %s, failure_code = %s, expires_at = %s, updated_at = %s
                    WHERE message_hash = %s
                    """,
                    (
                        record["status"],
                        record.get("sent_at"),
                        record.get("delivered_at"),
                        record.get("read_at"),
                        record.get("failed_at"),
                        record.get("failure_code", ""),
                        current + _retention(),
                        current,
                        key,
                    ),
                )
            return str(record["status"])

        def update(data: dict[str, Any]) -> str:
            record = data.setdefault("outbound_delivery", {}).get(key)
            if not isinstance(record, dict):
                return "unknown"
            self._apply_receipt(record, receipt.status, occurred, receipt.failure_code)
            record["expires_at"] = (current + _retention()).isoformat()
            record["updated_at"] = current.isoformat()
            return str(record["status"])

        return str(self.store._transaction(update))

    @staticmethod
    def _apply_receipt(record: dict[str, Any], status: str, occurred: datetime, failure_code: str) -> None:
        if status in _SUCCESS_FIELDS:
            field = _SUCCESS_FIELDS[status]
            record[field] = _serialize_time(_minimum(_as_datetime(record.get(field)), occurred))
        elif status == "failed":
            record["failed_at"] = _serialize_time(_maximum(_as_datetime(record.get("failed_at")), occurred))
            if failure_code:
                record["failure_code"] = failure_code[:40]
        record["status"] = _derive_status(record)

    def state(self, message_id: str) -> dict[str, Any] | None:
        key = _message_hash(message_id)
        fields = (
            "message_kind", "status", "accepted_at", "sent_at", "delivered_at",
            "read_at", "failed_at", "failure_code", "expires_at", "created_at", "updated_at",
        )
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                row = connection.execute(
                    "SELECT " + ", ".join(fields) + " FROM outbound_delivery_messages WHERE message_hash = %s",
                    (key,),
                ).fetchone()
            if not row:
                return None
            payload = dict(row)
            for field in fields:
                if isinstance(payload.get(field), datetime):
                    payload[field] = payload[field].astimezone(UTC).isoformat()
            return payload
        record = self.store.snapshot().get("outbound_delivery", {}).get(key)
        if not isinstance(record, dict):
            return None
        return {field: deepcopy(record.get(field)) for field in fields}

    def aggregate(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = _now(now)
        cutoff = current - timedelta(hours=24)
        pending_cutoff = current - timedelta(minutes=15)
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                rows = connection.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM outbound_delivery_messages
                    WHERE accepted_at >= %s AND expires_at > %s
                    GROUP BY status
                    """,
                    (cutoff, current),
                ).fetchall()
                aggregate = connection.execute(
                    """
                    SELECT
                        COUNT(*) FILTER (
                            WHERE status IN ('accepted', 'sent') AND accepted_at <= %s AND expires_at > %s
                        ) AS pending_over_15m,
                        COALESCE(EXTRACT(EPOCH FROM (%s - MIN(accepted_at))) FILTER (
                            WHERE status IN ('accepted', 'sent') AND expires_at > %s
                        ), 0) AS oldest_pending_age_seconds
                    FROM outbound_delivery_messages
                    """,
                    (pending_cutoff, current, current, current),
                ).fetchone()
            counts = {"accepted": 0, "sent": 0, "delivered": 0, "read": 0, "failed": 0}
            for row in rows:
                status = str(row.get("status") or "")
                if status in counts:
                    counts[status] = _integer(row.get("count"))
            pending = _integer(aggregate.get("pending_over_15m") if aggregate else 0)
            oldest = max(0, _integer(aggregate.get("oldest_pending_age_seconds") if aggregate else 0))
            return self._aggregate_payload(counts, pending, oldest)

        counts = {"accepted": 0, "sent": 0, "delivered": 0, "read": 0, "failed": 0}
        pending = 0
        oldest = 0
        for record in self.store.snapshot().get("outbound_delivery", {}).values():
            if not isinstance(record, dict):
                continue
            accepted_at = _as_datetime(record.get("accepted_at"))
            expires_at = _as_datetime(record.get("expires_at"))
            status = str(record.get("status") or "")
            if not accepted_at or (expires_at and expires_at <= current):
                continue
            if accepted_at >= cutoff and status in counts:
                counts[status] += 1
            if status in {"accepted", "sent"}:
                age = max(0, int((current - accepted_at).total_seconds()))
                oldest = max(oldest, age)
                if accepted_at <= pending_cutoff:
                    pending += 1
        return self._aggregate_payload(counts, pending, oldest)

    @staticmethod
    def _aggregate_payload(counts: dict[str, int], pending: int, oldest: int) -> dict[str, Any]:
        tracked = sum(counts.values())
        successes = counts["delivered"] + counts["read"]
        terminal = successes + counts["failed"]
        success_rate = round((successes / terminal) * 100, 1) if terminal else 0.0
        return {
            "tracked_24h": tracked,
            "by_status": counts,
            "terminal_24h": terminal,
            "delivery_success_pct": success_rate,
            "pending_over_15m": pending,
            "oldest_pending_age_seconds": oldest,
        }

    def cleanup(self, *, now: datetime | None = None) -> int:
        current = _now(now)
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                result = connection.execute(
                    "DELETE FROM outbound_delivery_messages WHERE expires_at <= %s",
                    (current,),
                )
            return int(result.rowcount or 0)

        def update(data: dict[str, Any]) -> int:
            messages = data.setdefault("outbound_delivery", {})
            removable = [
                key for key, record in messages.items()
                if isinstance(record, dict) and (_as_datetime(record.get("expires_at")) or current) <= current
            ]
            for key in removable:
                messages.pop(key, None)
            return len(removable)

        return int(self.store._transaction(update))


def record_receipts(repository: OutboundDeliveryRepository, receipts: Iterable[DeliveryReceipt]) -> dict[str, int]:
    result = {"updated": 0, "unknown": 0}
    for receipt in receipts:
        status = repository.record_receipt(receipt)
        result["unknown" if status == "unknown" else "updated"] += 1
    return result
