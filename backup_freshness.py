"""Privacy-safe operational checkpoint for encrypted PostgreSQL backups.

The singleton state contains only timestamps, a generic status/code, artifact integrity
metadata, encryption state, and schema identity. It never stores filenames, paths, database
URLs, phone identifiers, user content, ciphertext, provider payloads, or credentials.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from schema_recovery import require_current_manifest_schema


class BackupFreshnessError(RuntimeError):
    """Backup checkpoint failure identified by a non-sensitive operational code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_SCOPE = "production"
_HEX64 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    return current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)


def _database_url(value: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned.startswith(("postgresql://", "postgres://")):
        raise ValueError("DATABASE_URL must be a PostgreSQL connection URL")
    return cleaned


def _failure_code(value: str) -> str:
    cleaned = str(value or "").strip().casefold()
    return cleaned if _SAFE_CODE.fullmatch(cleaned) else "backup_failed"


def safe_backup_failure_code(exc: BaseException) -> str:
    """Map an exception to a bounded code without returning its message."""
    code = getattr(exc, "code", "")
    if isinstance(code, str) and _SAFE_CODE.fullmatch(code.casefold()):
        return code.casefold()
    if isinstance(exc, PermissionError):
        return "backup_permission_denied"
    if isinstance(exc, ValueError):
        return "backup_configuration_invalid"
    if isinstance(exc, OSError):
        return "backup_io_failed"
    if isinstance(exc, RuntimeError):
        message = str(exc or "").strip().casefold()
        if _SAFE_CODE.fullmatch(message):
            return message
        return "backup_runtime_failed"
    return "backup_failed"


def _connect(database_url: str):
    return psycopg.connect(_database_url(database_url), row_factory=dict_row)


def record_backup_success(
    database_url: str,
    manifest: dict[str, Any],
    *,
    now: datetime | None = None,
) -> None:
    """Record success only after artifact creation and schema-bound manifest validation."""
    identity = require_current_manifest_schema(manifest)
    artifact_hash = str(manifest.get("artifact_sha256") or "").strip().lower()
    if not _HEX64.fullmatch(artifact_hash):
        raise BackupFreshnessError("backup_artifact_hash_invalid")
    try:
        artifact_size = max(1, int(manifest.get("artifact_size_bytes") or 0))
    except (TypeError, ValueError):
        raise BackupFreshnessError("backup_artifact_size_invalid") from None
    if not bool(manifest.get("encrypted")):
        raise BackupFreshnessError("backup_artifact_unencrypted")

    current = _now(now)
    try:
        with _connect(database_url) as connection:
            connection.execute(
                """
                INSERT INTO backup_operational_state
                    (scope, last_attempt_at, last_success_at, last_failure_at, last_status,
                     last_failure_code, artifact_sha256, artifact_size_bytes,
                     schema_version, schema_checksum, encrypted, updated_at)
                VALUES (%s, %s, %s, NULL, 'success', '', %s, %s, %s, %s, TRUE, %s)
                ON CONFLICT (scope) DO UPDATE
                SET last_attempt_at = EXCLUDED.last_attempt_at,
                    last_success_at = EXCLUDED.last_success_at,
                    last_status = 'success',
                    last_failure_code = '',
                    artifact_sha256 = EXCLUDED.artifact_sha256,
                    artifact_size_bytes = EXCLUDED.artifact_size_bytes,
                    schema_version = EXCLUDED.schema_version,
                    schema_checksum = EXCLUDED.schema_checksum,
                    encrypted = TRUE,
                    updated_at = EXCLUDED.updated_at
                """,
                (
                    _SCOPE,
                    current,
                    current,
                    artifact_hash,
                    artifact_size,
                    identity.version,
                    identity.checksum,
                    current,
                ),
            )
    except psycopg.Error as exc:
        raise BackupFreshnessError("backup_checkpoint_write_failed") from exc


def record_backup_failure(
    database_url: str,
    code: str,
    *,
    now: datetime | None = None,
) -> None:
    """Record a generic failed attempt while preserving the last successful artifact."""
    current = _now(now)
    safe_code = _failure_code(code)
    try:
        with _connect(database_url) as connection:
            connection.execute(
                """
                INSERT INTO backup_operational_state
                    (scope, last_attempt_at, last_success_at, last_failure_at, last_status,
                     last_failure_code, artifact_sha256, artifact_size_bytes,
                     schema_version, schema_checksum, encrypted, updated_at)
                VALUES (%s, %s, NULL, %s, 'failed', %s, '', 0, 0, '', FALSE, %s)
                ON CONFLICT (scope) DO UPDATE
                SET last_attempt_at = EXCLUDED.last_attempt_at,
                    last_failure_at = EXCLUDED.last_failure_at,
                    last_status = 'failed',
                    last_failure_code = EXCLUDED.last_failure_code,
                    updated_at = EXCLUDED.updated_at
                """,
                (_SCOPE, current, current, safe_code, current),
            )
    except psycopg.Error as exc:
        raise BackupFreshnessError("backup_checkpoint_write_failed") from exc


def aggregate_backup_freshness(
    store: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return aggregate operator state without exposing artifact identity or connection data."""
    if str(getattr(store, "backend_name", "json")) != "postgresql":
        return {
            "state": "unavailable",
            "last_status": "unknown",
            "last_success_at": None,
            "last_attempt_at": None,
            "age_hours": None,
            "encrypted": False,
            "schema_version": 0,
            "artifact_size_bytes": 0,
            "last_failure_code": "",
        }

    try:
        with store.pool.connection() as connection:
            row = connection.execute(
                """
                SELECT last_attempt_at, last_success_at, last_status, last_failure_code,
                       artifact_size_bytes, schema_version, encrypted
                FROM backup_operational_state
                WHERE scope = %s
                """,
                (_SCOPE,),
            ).fetchone()
    except Exception:
        return {
            "state": "unavailable",
            "last_status": "unknown",
            "last_success_at": None,
            "last_attempt_at": None,
            "age_hours": None,
            "encrypted": False,
            "schema_version": 0,
            "artifact_size_bytes": 0,
            "last_failure_code": "backup_checkpoint_read_failed",
        }

    if not row:
        return {
            "state": "missing",
            "last_status": "never",
            "last_success_at": None,
            "last_attempt_at": None,
            "age_hours": None,
            "encrypted": False,
            "schema_version": 0,
            "artifact_size_bytes": 0,
            "last_failure_code": "",
        }

    current = _now(now)
    success = row.get("last_success_at")
    attempt = row.get("last_attempt_at")
    if isinstance(success, datetime):
        success = success.replace(tzinfo=UTC) if success.tzinfo is None else success.astimezone(UTC)
    else:
        success = None
    if isinstance(attempt, datetime):
        attempt = attempt.replace(tzinfo=UTC) if attempt.tzinfo is None else attempt.astimezone(UTC)
    else:
        attempt = None
    age_hours = None
    state = "missing"
    if success is not None:
        delta = current - success
        if delta.total_seconds() < 0:
            state = "invalid"
        else:
            age_hours = round(delta.total_seconds() / 3600.0, 1)
            state = "recorded"

    return {
        "state": state,
        "last_status": str(row.get("last_status") or "unknown")[:20],
        "last_success_at": success.isoformat() if success else None,
        "last_attempt_at": attempt.isoformat() if attempt else None,
        "age_hours": age_hours,
        "encrypted": bool(row.get("encrypted")),
        "schema_version": max(0, int(row.get("schema_version") or 0)),
        "artifact_size_bytes": max(0, int(row.get("artifact_size_bytes") or 0)),
        "last_failure_code": _failure_code(str(row.get("last_failure_code") or "")) if row.get("last_failure_code") else "",
    }
