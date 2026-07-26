"""Short-lived pending document actions without retaining document contents."""
from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg.types.json import Jsonb


def _phone_hash(phone: str) -> str:
    normalized = "".join(character for character in str(phone or "") if character.isdigit() or character == "+")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    return current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _sanitize(action: dict[str, Any]) -> dict[str, Any]:
    limits = {"title": 180, "topic": 80, "due_at": 10, "next_step": 300, "authority": 80, "source_kind": 20}
    clean: dict[str, Any] = {}
    for key, limit in limits.items():
        value = action.get(key)
        if value in (None, ""):
            continue
        clean[key] = " ".join(str(value).split())[:limit]
    if clean.get("due_at"):
        try:
            datetime.strptime(str(clean["due_at"]), "%Y-%m-%d")
        except ValueError:
            clean.pop("due_at", None)
    return clean


class PendingDocumentRepository:
    """Stores only a generic action proposal for at most 24 hours."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.backend_name = str(getattr(store, "backend_name", "json"))
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS pending_document_actions (
                        phone_hash TEXT PRIMARY KEY,
                        action JSONB NOT NULL DEFAULT '{}'::jsonb,
                        expires_at TIMESTAMPTZ NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS pending_document_actions_expiry_idx ON pending_document_actions (expires_at)"
                )

    def put(
        self,
        phone: str,
        action: dict[str, Any] | None,
        *,
        now: datetime | None = None,
        ttl: timedelta = timedelta(hours=24),
    ) -> dict[str, Any] | None:
        if not action:
            self.delete(phone)
            return None
        clean = _sanitize(action)
        if not clean.get("title"):
            self.delete(phone)
            return None
        key = _phone_hash(phone)
        current = _now(now)
        expires = current + ttl
        record = {"action": clean, "expires_at": expires.isoformat(), "created_at": current.isoformat()}
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                connection.execute(
                    """
                    INSERT INTO pending_document_actions (phone_hash, action, expires_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (phone_hash) DO UPDATE
                    SET action = EXCLUDED.action, expires_at = EXCLUDED.expires_at, updated_at = NOW()
                    """,
                    (key, Jsonb(clean), expires),
                )
            return deepcopy(record)

        def save(data: dict[str, Any]) -> dict[str, Any]:
            data.setdefault("pending_document_actions", {})[key] = deepcopy(record)
            return deepcopy(record)

        return self.store._transaction(save)

    def get(self, phone: str, *, now: datetime | None = None) -> dict[str, Any] | None:
        key = _phone_hash(phone)
        current = _now(now)
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                row = connection.execute(
                    "SELECT action, expires_at FROM pending_document_actions WHERE phone_hash = %s",
                    (key,),
                ).fetchone()
                if not row:
                    return None
                expires = _as_datetime(row["expires_at"])
                if not expires or expires <= current:
                    connection.execute("DELETE FROM pending_document_actions WHERE phone_hash = %s", (key,))
                    return None
                return deepcopy(dict(row["action"] or {}))

        snapshot = self.store.snapshot()
        record = snapshot.get("pending_document_actions", {}).get(key)
        if not isinstance(record, dict):
            return None
        expires = _as_datetime(record.get("expires_at"))
        if not expires or expires <= current:
            self.delete(phone)
            return None
        action = record.get("action")
        return deepcopy(action) if isinstance(action, dict) else None

    def delete(self, phone: str) -> bool:
        key = _phone_hash(phone)
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                cursor = connection.execute("DELETE FROM pending_document_actions WHERE phone_hash = %s", (key,))
            return cursor.rowcount > 0

        def remove(data: dict[str, Any]) -> bool:
            return data.setdefault("pending_document_actions", {}).pop(key, None) is not None

        return bool(self.store._transaction(remove))

    def cleanup_expired(self, *, now: datetime | None = None) -> int:
        current = _now(now)
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                cursor = connection.execute("DELETE FROM pending_document_actions WHERE expires_at <= %s", (current,))
            return max(cursor.rowcount, 0)

        def cleanup(data: dict[str, Any]) -> int:
            records = data.setdefault("pending_document_actions", {})
            expired = []
            for key, record in records.items():
                expires = _as_datetime(record.get("expires_at")) if isinstance(record, dict) else None
                if not expires or expires <= current:
                    expired.append(key)
            for key in expired:
                del records[key]
            return len(expired)

        return int(self.store._transaction(cleanup))
