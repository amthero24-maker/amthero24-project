"""Durable storage for AmtHero24 with PostgreSQL and JSON fallback."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, TypeVar

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

T = TypeVar("T")
logger = logging.getLogger("amthero24.storage")

_ALLOWED_USER_FIELDS = {
    # Consent-backed long-term memory.
    "first_name", "city", "preferred_language", "current_topic", "last_assistant_reply",
    "conversation_summary", "communication_style",
    # Consent and onboarding state.
    "memory_consent", "memory_consent_at", "memory_consent_version", "onboarding_stage",
    "intro_sent_at", "pending_name", "pending_name_expires_at", "name_prompted",
    # Short-lived operational context; cleared after 24 hours.
    "session_language", "session_topic", "session_last_reply", "session_expires_at",
    # Operational metadata.
    "last_seen", "last_message", "last_message_type",
}
_FIELD_LIMITS = {
    "first_name": 80,
    "city": 80,
    "last_assistant_reply": 1800,
    "conversation_summary": 600,
    "session_last_reply": 1800,
    "last_message": 300,
    "pending_name": 80,
    "communication_style": 80,
}
_SESSION_FIELDS = {
    "session_language", "session_topic", "session_last_reply", "session_expires_at",
    "last_message", "last_message_type",
}
_POSTGRES_SINGLETON: PostgresDataStore | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _phone_hash(phone: str) -> str:
    normalized = "".join(ch for ch in phone if ch.isdigit() or ch == "+")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _clean_updates(updates: dict[str, Any]) -> dict[str, Any]:
    clean = {key: value for key, value in updates.items() if key in _ALLOWED_USER_FIELDS}
    for key, limit in _FIELD_LIMITS.items():
        if key in clean:
            clean[key] = str(clean[key])[:limit]
    clean["updated_at"] = _now()
    return clean


def _as_datetime(value: Any, fallback: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            parsed = fallback or datetime.now(UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class JsonDataStore:
    """Thread-safe atomic JSON fallback used locally and when PostgreSQL is unavailable."""

    backend_name = "json"

    def __new__(cls, path: str | Path) -> JsonDataStore | PostgresDataStore:
        if cls is JsonDataStore:
            database_url = os.getenv("DATABASE_URL", "").strip()
            if database_url:
                global _POSTGRES_SINGLETON
                if _POSTGRES_SINGLETON is not None:
                    return _POSTGRES_SINGLETON
                try:
                    postgres = PostgresDataStore(database_url)
                    postgres.migrate_json(path)
                    _POSTGRES_SINGLETON = postgres
                    logger.info("Using PostgreSQL storage backend")
                    return postgres
                except Exception:
                    logger.exception("PostgreSQL unavailable; falling back to atomic JSON storage")
        return super().__new__(cls)

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"users": {}, "messages": {}, "cases": {}, "audit_log": []}

    def _read_unlocked(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            parsed = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty()
        return parsed if isinstance(parsed, dict) else self._empty()

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def _transaction(self, operation: Callable[[dict[str, Any]], T]) -> T:
        with self._lock:
            data = self._read_unlocked()
            for key, default in self._empty().items():
                data.setdefault(key, default)
            result = operation(data)
            self._write_unlocked(data)
            return result

    def claim_message(
        self,
        message_id: str,
        phone: str,
        text: str = "",
        *,
        message_type: str = "text",
        media_id: str | None = None,
    ) -> bool:
        if not message_id or not phone:
            return False

        def claim(data: dict[str, Any]) -> bool:
            if message_id in data["messages"]:
                return False
            now = _now()
            data["messages"][message_id] = {
                "phone_hash": _phone_hash(phone),
                "text": text[:2000],
                "type": message_type,
                "has_media": bool(media_id),
                "status": "claimed",
                "created_at": now,
                "updated_at": now,
            }
            return True

        return self._transaction(claim)

    def update_message_status(self, message_id: str, status: str) -> None:
        def update(data: dict[str, Any]) -> None:
            record = data["messages"].get(message_id)
            if record:
                record["status"] = status
                record["updated_at"] = _now()

        self._transaction(update)

    def get_user(self, phone: str) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._read_unlocked().get("users", {}).get(_phone_hash(phone), {}))

    def update_user(self, phone: str, updates: dict[str, Any]) -> dict[str, Any]:
        clean = _clean_updates(updates)
        key = _phone_hash(phone)

        def update(data: dict[str, Any]) -> dict[str, Any]:
            profile = data["users"].setdefault(key, {})
            profile.update(clean)
            return deepcopy(profile)

        return self._transaction(update)

    def remove_user_fields(self, phone: str, fields: set[str] | list[str] | tuple[str, ...]) -> dict[str, Any]:
        key = _phone_hash(phone)
        requested = set(fields)

        def remove(data: dict[str, Any]) -> dict[str, Any]:
            profile = data["users"].setdefault(key, {})
            for field in requested:
                profile.pop(field, None)
            profile["updated_at"] = _now()
            return deepcopy(profile)

        return self._transaction(remove)

    def delete_user(self, phone: str) -> bool:
        key = _phone_hash(phone)

        def delete(data: dict[str, Any]) -> bool:
            existed = data["users"].pop(key, None) is not None
            for message_id in [mid for mid, record in data["messages"].items() if record.get("phone_hash") == key]:
                del data["messages"][message_id]
            return existed

        return self._transaction(delete)

    def recent_user_messages(self, phone: str, limit: int = 4) -> list[str]:
        key = _phone_hash(phone)
        with self._lock:
            records = [
                record
                for record in self._read_unlocked().get("messages", {}).values()
                if record.get("phone_hash") == key and record.get("text")
            ]
        records.sort(key=lambda record: record.get("created_at", ""))
        return [str(record["text"]) for record in records[-limit:]]

    def cleanup_expired(
        self,
        now: datetime | None = None,
        *,
        max_age: timedelta = timedelta(hours=24),
    ) -> int:
        current = now or datetime.now(UTC)
        cutoff = current - max_age

        def cleanup(data: dict[str, Any]) -> int:
            cleaned = 0
            expired_messages: list[str] = []
            for message_id, record in data["messages"].items():
                created = _as_datetime(record.get("created_at"), fallback=datetime.min.replace(tzinfo=UTC))
                if created < cutoff:
                    expired_messages.append(message_id)
            for message_id in expired_messages:
                del data["messages"][message_id]
                cleaned += 1

            for profile in data["users"].values():
                session_expiry = _as_datetime(profile.get("session_expires_at"), fallback=current)
                if profile.get("session_expires_at") and session_expiry < current:
                    for field in _SESSION_FIELDS:
                        profile.pop(field, None)
                    profile["updated_at"] = _now()
                    cleaned += 1

                pending_expiry = _as_datetime(profile.get("pending_name_expires_at"), fallback=current)
                if profile.get("pending_name_expires_at") and pending_expiry < current:
                    profile.pop("pending_name", None)
                    profile.pop("pending_name_expires_at", None)
                    if profile.get("onboarding_stage") == "awaiting_consent":
                        profile["onboarding_stage"] = "awaiting_name"
                    profile["updated_at"] = _now()
                    cleaned += 1
            return cleaned

        return self._transaction(cleanup)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._read_unlocked())

    def close(self) -> None:
        return None


class PostgresDataStore:
    """PostgreSQL-backed store preserving the JsonDataStore interface."""

    backend_name = "postgresql"

    def __init__(self, database_url: str) -> None:
        self.pool = ConnectionPool(
            conninfo=database_url,
            min_size=1,
            max_size=max(2, int(os.getenv("DB_POOL_MAX", "5"))),
            open=True,
            timeout=10,
            kwargs={"row_factory": dict_row},
        )
        self.pool.wait(timeout=15)
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS hero_users (
                phone_hash TEXT PRIMARY KEY,
                profile JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS inbound_messages (
                message_id TEXT PRIMARY KEY,
                phone_hash TEXT NOT NULL,
                text TEXT NOT NULL DEFAULT '',
                message_type TEXT NOT NULL DEFAULT 'text',
                has_media BOOLEAN NOT NULL DEFAULT FALSE,
                status TEXT NOT NULL DEFAULT 'claimed',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS inbound_messages_phone_created_idx
            ON inbound_messages (phone_hash, created_at DESC)
            """,
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
        )
        with self.pool.connection() as conn:
            for statement in statements:
                conn.execute(statement)

    def claim_message(
        self,
        message_id: str,
        phone: str,
        text: str = "",
        *,
        message_type: str = "text",
        media_id: str | None = None,
    ) -> bool:
        if not message_id or not phone:
            return False
        with self.pool.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO inbound_messages
                    (message_id, phone_hash, text, message_type, has_media, status)
                VALUES (%s, %s, %s, %s, %s, 'claimed')
                ON CONFLICT (message_id) DO NOTHING
                """,
                (message_id, _phone_hash(phone), text[:2000], message_type, bool(media_id)),
            )
            return cursor.rowcount == 1

    def update_message_status(self, message_id: str, status: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """
                UPDATE inbound_messages
                SET status = %s, updated_at = NOW()
                WHERE message_id = %s
                """,
                (status[:40], message_id),
            )

    def get_user(self, phone: str) -> dict[str, Any]:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT profile FROM hero_users WHERE phone_hash = %s",
                (_phone_hash(phone),),
            ).fetchone()
        return deepcopy(dict(row["profile"])) if row else {}

    def update_user(self, phone: str, updates: dict[str, Any]) -> dict[str, Any]:
        clean = _clean_updates(updates)
        with self.pool.connection() as conn:
            row = conn.execute(
                """
                INSERT INTO hero_users (phone_hash, profile)
                VALUES (%s, %s)
                ON CONFLICT (phone_hash) DO UPDATE
                SET profile = hero_users.profile || EXCLUDED.profile,
                    updated_at = NOW()
                RETURNING profile
                """,
                (_phone_hash(phone), Jsonb(clean)),
            ).fetchone()
        return deepcopy(dict(row["profile"])) if row else {}

    def remove_user_fields(self, phone: str, fields: set[str] | list[str] | tuple[str, ...]) -> dict[str, Any]:
        key = _phone_hash(phone)
        requested = set(fields)
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT profile FROM hero_users WHERE phone_hash = %s FOR UPDATE",
                (key,),
            ).fetchone()
            profile = dict(row["profile"]) if row else {}
            for field in requested:
                profile.pop(field, None)
            profile["updated_at"] = _now()
            saved = conn.execute(
                """
                INSERT INTO hero_users (phone_hash, profile)
                VALUES (%s, %s)
                ON CONFLICT (phone_hash) DO UPDATE
                SET profile = EXCLUDED.profile, updated_at = NOW()
                RETURNING profile
                """,
                (key, Jsonb(profile)),
            ).fetchone()
        return deepcopy(dict(saved["profile"])) if saved else {}

    def delete_user(self, phone: str) -> bool:
        key = _phone_hash(phone)
        with self.pool.connection() as conn:
            conn.execute("DELETE FROM inbound_messages WHERE phone_hash = %s", (key,))
            cursor = conn.execute("DELETE FROM hero_users WHERE phone_hash = %s", (key,))
            return cursor.rowcount == 1

    def recent_user_messages(self, phone: str, limit: int = 4) -> list[str]:
        safe_limit = max(1, min(int(limit), 20))
        with self.pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT text
                FROM inbound_messages
                WHERE phone_hash = %s AND text <> ''
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (_phone_hash(phone), safe_limit),
            ).fetchall()
        return [str(row["text"]) for row in reversed(rows)]

    def cleanup_expired(
        self,
        now: datetime | None = None,
        *,
        max_age: timedelta = timedelta(hours=24),
    ) -> int:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        cutoff = current - max_age
        cleaned = 0
        with self.pool.connection() as conn:
            deleted = conn.execute(
                "DELETE FROM inbound_messages WHERE created_at < %s",
                (cutoff,),
            )
            cleaned += max(deleted.rowcount, 0)

            rows = conn.execute("SELECT phone_hash, profile FROM hero_users").fetchall()
            for row in rows:
                profile = dict(row["profile"])
                changed = False

                if profile.get("session_expires_at"):
                    session_expiry = _as_datetime(profile["session_expires_at"], fallback=current)
                    if session_expiry < current:
                        for field in _SESSION_FIELDS:
                            profile.pop(field, None)
                        changed = True

                if profile.get("pending_name_expires_at"):
                    pending_expiry = _as_datetime(profile["pending_name_expires_at"], fallback=current)
                    if pending_expiry < current:
                        profile.pop("pending_name", None)
                        profile.pop("pending_name_expires_at", None)
                        if profile.get("onboarding_stage") == "awaiting_consent":
                            profile["onboarding_stage"] = "awaiting_name"
                        changed = True

                if changed:
                    profile["updated_at"] = _now()
                    conn.execute(
                        """
                        UPDATE hero_users
                        SET profile = %s, updated_at = NOW()
                        WHERE phone_hash = %s
                        """,
                        (Jsonb(profile), row["phone_hash"]),
                    )
                    cleaned += 1
        return cleaned

    def snapshot(self) -> dict[str, Any]:
        with self.pool.connection() as conn:
            user_rows = conn.execute("SELECT phone_hash, profile FROM hero_users").fetchall()
            message_rows = conn.execute(
                """
                SELECT message_id, phone_hash, text, message_type, has_media, status,
                       created_at, updated_at
                FROM inbound_messages
                """
            ).fetchall()
        users = {str(row["phone_hash"]): dict(row["profile"]) for row in user_rows}
        messages = {
            str(row["message_id"]): {
                "phone_hash": row["phone_hash"],
                "text": row["text"],
                "type": row["message_type"],
                "has_media": row["has_media"],
                "status": row["status"],
                "created_at": _as_datetime(row["created_at"]).isoformat(),
                "updated_at": _as_datetime(row["updated_at"]).isoformat(),
            }
            for row in message_rows
        }
        return {"users": users, "messages": messages, "cases": {}, "audit_log": []}

    def migrate_json(self, path: str | Path) -> int:
        source = Path(path)
        if not source.exists():
            return 0
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Skipping invalid JSON migration source: %s", source)
            return 0
        if not isinstance(payload, dict):
            return 0

        migration_name = "json_store_import_v1"
        with self.pool.connection() as conn:
            already_done = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE name = %s",
                (migration_name,),
            ).fetchone()
            if already_done:
                return 0

            imported = 0
            for phone_hash, profile in payload.get("users", {}).items():
                if not isinstance(profile, dict):
                    continue
                conn.execute(
                    """
                    INSERT INTO hero_users (phone_hash, profile)
                    VALUES (%s, %s)
                    ON CONFLICT (phone_hash) DO UPDATE
                    SET profile = hero_users.profile || EXCLUDED.profile,
                        updated_at = NOW()
                    """,
                    (str(phone_hash), Jsonb(profile)),
                )
                imported += 1

            for message_id, record in payload.get("messages", {}).items():
                if not isinstance(record, dict):
                    continue
                created_at = _as_datetime(record.get("created_at"))
                updated_at = _as_datetime(record.get("updated_at"), fallback=created_at)
                conn.execute(
                    """
                    INSERT INTO inbound_messages
                        (message_id, phone_hash, text, message_type, has_media, status, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (message_id) DO NOTHING
                    """,
                    (
                        str(message_id),
                        str(record.get("phone_hash") or ""),
                        str(record.get("text") or "")[:2000],
                        str(record.get("type") or "text"),
                        bool(record.get("has_media")),
                        str(record.get("status") or "claimed")[:40],
                        created_at,
                        updated_at,
                    ),
                )
                imported += 1

            conn.execute(
                "INSERT INTO schema_migrations (name) VALUES (%s)",
                (migration_name,),
            )
        if imported:
            logger.info("Imported %s JSON records into PostgreSQL", imported)
        return imported

    def close(self) -> None:
        self.pool.close()


_default_store: JsonDataStore | PostgresDataStore | None = None


def _get_default_store() -> JsonDataStore | PostgresDataStore:
    global _default_store
    if _default_store is None:
        _default_store = JsonDataStore(os.getenv("DATA_STORE_PATH", "data/store.json"))
    return _default_store


def add_message(msg_id: str, sender: str, text: str) -> bool:
    return _get_default_store().claim_message(msg_id, sender, text)


def add_user(phone: str, data: dict[str, Any]) -> dict[str, Any]:
    return _get_default_store().update_user(phone, data)


def get_store() -> dict[str, Any]:
    return _get_default_store().snapshot()


def _load() -> dict[str, Any]:
    return _get_default_store().snapshot()


def _save_atomic(data: dict[str, Any]) -> None:
    store = _get_default_store()
    if not isinstance(store, JsonDataStore):
        raise RuntimeError("_save_atomic is only available with the JSON fallback backend")
    with store._lock:
        store._write_unlocked(data)
