"""Encrypted PostgreSQL work queue for crash-safe inbound WhatsApp processing.

The queue stores a short-lived encrypted sender and media identifier only while work is
pending. Completed and dead-letter rows are cryptographically erased by clearing their
ciphertext fields. Message text and type remain in the existing retention-controlled
``inbound_messages`` table and are not duplicated.
"""
from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from encryption_policy import assess_secret


class QueueServiceError(RuntimeError):
    """Safe operational queue failure identified by a non-sensitive code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class QueueItem:
    message_id: str
    sender: str
    text: str
    message_type: str
    media_id: str | None
    mime_type: str
    inbound_status: str
    attempt_count: int


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    return current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)


def _flag(name: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).strip().casefold() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError:
        value = default
    return min(max(value, minimum), maximum)


def queue_enabled() -> bool:
    return _flag("DURABLE_QUEUE_ENABLED", False)


def queue_encryption_status() -> str:
    return assess_secret("MESSAGE_QUEUE_ENCRYPTION_KEY").status


def queue_status(store: Any) -> str:
    if not queue_enabled():
        return "disabled"
    if str(getattr(store, "backend_name", "json")) != "postgresql":
        return "requires-postgresql"
    return "configured" if queue_encryption_status() == "configured" else "misconfigured"


def queue_poll_seconds() -> int:
    return _int_env("DURABLE_QUEUE_POLL_SECONDS", 5, 1, 300)


def _max_attempts() -> int:
    return _int_env("DURABLE_QUEUE_MAX_ATTEMPTS", 5, 1, 20)


def _envelope_lifetime() -> timedelta:
    return timedelta(hours=_int_env("DURABLE_QUEUE_ENVELOPE_HOURS", 48, 1, 168))


def _completed_retention() -> timedelta:
    return timedelta(hours=_int_env("DURABLE_QUEUE_COMPLETED_RETENTION_HOURS", 24, 1, 168))


def _processing_lease() -> timedelta:
    return timedelta(minutes=_int_env("DURABLE_QUEUE_LEASE_MINUTES", 15, 2, 120))


def _fernet() -> Fernet:
    assessment = assess_secret("MESSAGE_QUEUE_ENCRYPTION_KEY")
    if not assessment.ready:
        raise QueueServiceError("queue_encryption_not_configured")
    secret = os.getenv("MESSAGE_QUEUE_ENCRYPTION_KEY", "").strip()
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def _encrypt(value: str) -> str:
    if not value:
        return ""
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError) as exc:
        raise QueueServiceError("queue_decryption_failed") from exc


class DurableQueueRepository:
    """PostgreSQL queue using leases and ``SKIP LOCKED`` for replica-safe workers."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.backend_name = str(getattr(store, "backend_name", "json"))
        if self.backend_name == "postgresql":
            self._initialize_postgres_schema()

    def _require_postgres(self) -> None:
        if self.backend_name != "postgresql":
            raise QueueServiceError("queue_requires_postgresql")

    def _initialize_postgres_schema(self) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS inbound_work_queue (
                message_id TEXT PRIMARY KEY REFERENCES inbound_messages(message_id) ON DELETE CASCADE,
                sender_ciphertext TEXT NOT NULL,
                media_id_ciphertext TEXT,
                mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',
                status TEXT NOT NULL DEFAULT 'queued',
                available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                lease_until TIMESTAMPTZ,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_failure_code TEXT NOT NULL DEFAULT '',
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS inbound_work_queue_ready_idx
            ON inbound_work_queue (status, available_at, lease_until)
            """,
            "CREATE INDEX IF NOT EXISTS inbound_work_queue_expiry_idx ON inbound_work_queue (expires_at)",
        )
        with self.store.pool.connection() as connection:
            for statement in statements:
                connection.execute(statement)

    def enqueue(
        self,
        message_id: str,
        sender: str,
        *,
        media_id: str | None = None,
        mime_type: str = "application/octet-stream",
        now: datetime | None = None,
    ) -> None:
        self._require_postgres()
        clean_id = str(message_id or "").strip()
        clean_sender = str(sender or "").strip()
        if not clean_id or not clean_sender:
            raise QueueServiceError("invalid_queue_envelope")
        current = _now(now)
        sender_ciphertext = _encrypt(clean_sender)
        media_ciphertext = _encrypt(str(media_id or "")) if media_id else None
        expires_at = current + _envelope_lifetime()
        with self.store.pool.connection() as connection:
            row = connection.execute(
                "SELECT status FROM inbound_messages WHERE message_id = %s",
                (clean_id,),
            ).fetchone()
            if not row:
                raise QueueServiceError("message_claim_missing")
            connection.execute(
                """
                INSERT INTO inbound_work_queue
                    (message_id, sender_ciphertext, media_id_ciphertext, mime_type,
                     status, available_at, expires_at, created_at, updated_at)
                VALUES (%s, %s, %s, %s, 'queued', %s, %s, %s, %s)
                ON CONFLICT (message_id) DO UPDATE
                SET sender_ciphertext = EXCLUDED.sender_ciphertext,
                    media_id_ciphertext = EXCLUDED.media_id_ciphertext,
                    mime_type = EXCLUDED.mime_type,
                    status = 'queued',
                    available_at = EXCLUDED.available_at,
                    lease_until = NULL,
                    last_failure_code = '',
                    expires_at = EXCLUDED.expires_at,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    clean_id,
                    sender_ciphertext,
                    media_ciphertext,
                    str(mime_type or "application/octet-stream")[:120],
                    current,
                    expires_at,
                    current,
                    current,
                ),
            )

    def claim(self, message_id: str | None = None, *, now: datetime | None = None) -> QueueItem | None:
        self._require_postgres()
        current = _now(now)
        lease_until = current + _processing_lease()
        requested = str(message_id or "").strip() or None
        max_attempts = _max_attempts()
        with self.store.pool.connection() as connection:
            connection.execute(
                """
                UPDATE inbound_work_queue
                SET status = 'dead', sender_ciphertext = '', media_id_ciphertext = NULL,
                    lease_until = NULL, last_failure_code = 'max_attempts', updated_at = %s
                WHERE status IN ('queued', 'processing') AND attempt_count >= %s
                """,
                (current, max_attempts),
            )
            row = connection.execute(
                """
                WITH candidate AS (
                    SELECT message_id
                    FROM inbound_work_queue
                    WHERE expires_at > %s
                      AND attempt_count < %s
                      AND (
                            (status = 'queued' AND available_at <= %s)
                         OR (status = 'processing' AND (lease_until IS NULL OR lease_until <= %s))
                      )
                      AND (%s::TEXT IS NULL OR message_id = %s)
                    ORDER BY available_at ASC, created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                ), updated AS (
                    UPDATE inbound_work_queue AS queue
                    SET status = 'processing', lease_until = %s,
                        attempt_count = queue.attempt_count + 1, updated_at = %s
                    FROM candidate
                    WHERE queue.message_id = candidate.message_id
                    RETURNING queue.*
                )
                SELECT updated.message_id, updated.sender_ciphertext,
                       updated.media_id_ciphertext, updated.mime_type,
                       updated.attempt_count, messages.text,
                       messages.message_type, messages.status AS inbound_status
                FROM updated
                JOIN inbound_messages AS messages USING (message_id)
                """,
                (
                    current,
                    max_attempts,
                    current,
                    current,
                    requested,
                    requested,
                    lease_until,
                    current,
                ),
            ).fetchone()
        if not row:
            return None
        return QueueItem(
            message_id=str(row["message_id"]),
            sender=_decrypt(str(row["sender_ciphertext"])),
            text=str(row.get("text") or ""),
            message_type=str(row.get("message_type") or "text"),
            media_id=_decrypt(str(row["media_id_ciphertext"])) if row.get("media_id_ciphertext") else None,
            mime_type=str(row.get("mime_type") or "application/octet-stream"),
            inbound_status=str(row.get("inbound_status") or "processing"),
            attempt_count=int(row.get("attempt_count") or 0),
        )

    def complete(self, message_id: str, *, now: datetime | None = None) -> None:
        self._require_postgres()
        current = _now(now)
        with self.store.pool.connection() as connection:
            connection.execute(
                """
                UPDATE inbound_work_queue
                SET status = 'completed', sender_ciphertext = '', media_id_ciphertext = NULL,
                    lease_until = NULL, last_failure_code = '', updated_at = %s
                WHERE message_id = %s
                """,
                (current, str(message_id)),
            )

    def retry(self, message_id: str, code: str, *, now: datetime | None = None) -> str:
        self._require_postgres()
        current = _now(now)
        with self.store.pool.connection() as connection:
            row = connection.execute(
                "SELECT attempt_count FROM inbound_work_queue WHERE message_id = %s FOR UPDATE",
                (str(message_id),),
            ).fetchone()
            if not row:
                return "missing"
            attempts = int(row["attempt_count"] or 0)
            if attempts >= _max_attempts():
                connection.execute(
                    """
                    UPDATE inbound_work_queue
                    SET status = 'dead', sender_ciphertext = '', media_id_ciphertext = NULL,
                        lease_until = NULL, last_failure_code = %s, updated_at = %s
                    WHERE message_id = %s
                    """,
                    (str(code or "processing_error")[:80], current, str(message_id)),
                )
                return "dead"
            delay_seconds = min(15 * (2 ** max(attempts - 1, 0)), 900)
            connection.execute(
                """
                UPDATE inbound_work_queue
                SET status = 'queued', available_at = %s, lease_until = NULL,
                    last_failure_code = %s, updated_at = %s
                WHERE message_id = %s
                """,
                (
                    current + timedelta(seconds=delay_seconds),
                    str(code or "processing_error")[:80],
                    current,
                    str(message_id),
                ),
            )
            return "queued"

    def dead_letter(self, message_id: str, code: str, *, now: datetime | None = None) -> None:
        self._require_postgres()
        current = _now(now)
        with self.store.pool.connection() as connection:
            connection.execute(
                """
                UPDATE inbound_work_queue
                SET status = 'dead', sender_ciphertext = '', media_id_ciphertext = NULL,
                    lease_until = NULL, last_failure_code = %s, updated_at = %s
                WHERE message_id = %s
                """,
                (str(code or "unrecoverable")[:80], current, str(message_id)),
            )

    def cleanup(self, *, now: datetime | None = None) -> int:
        self._require_postgres()
        current = _now(now)
        completed_before = current - _completed_retention()
        with self.store.pool.connection() as connection:
            result = connection.execute(
                """
                DELETE FROM inbound_work_queue
                WHERE expires_at <= %s
                   OR (status IN ('completed', 'dead') AND updated_at <= %s)
                """,
                (current, completed_before),
            )
        return int(result.rowcount or 0)

    def aggregate(self) -> dict[str, int]:
        self._require_postgres()
        with self.store.pool.connection() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM inbound_work_queue GROUP BY status"
            ).fetchall()
        result = {"queued": 0, "processing": 0, "completed": 0, "dead": 0}
        for row in rows:
            status = str(row["status"])
            if status in result:
                result[status] = int(row["count"])
        return result
