"""Privacy-safe production backup recovery receipts and launch metrics.

The backup Cron records only bounded operational state in the existing provider telemetry
store: provider, operation, outcome, latency, a sanitized error category, and timestamp.
No artifact path, database URL, key, file content, user identifier, message, or document
content enters this layer.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping

from data_store import PostgresDataStore
from provider_reliability import ProviderReliabilityRepository, telemetry_enabled

_BACKUP_PROVIDER = "postgres_backup"
_BACKUP_OPERATION = "encrypted_backup"
_ALLOWED_OUTCOMES = {"started", "success", "failure"}
_DEFAULT_MAX_AGE_HOURS = 30
_FUTURE_TOLERANCE_SECONDS = 300
_INTERNAL_CODE_TYPES = {
    ("backup_recovery", "BackupRecoveryError"),
    ("scripts.verify_backup_volume", "BackupVolumeError"),
}
_KNOWN_FAILURE_CODES = {
    "backuprecoveryerror",
    "backupvolumeerror",
    "calledprocesserror",
    "database_url_invalid",
    "missing_mount_variable",
    "missing_output_directory",
    "mount_directory_missing",
    "mount_not_absolute",
    "mount_not_attached",
    "mountinfo_unavailable",
    "oserror",
    "outcome_invalid",
    "output_not_absolute",
    "output_outside_mount",
    "pg_dump_authentication_failed",
    "pg_dump_connection_failed",
    "pg_dump_database_error",
    "pg_dump_failed",
    "pg_dump_permission_denied",
    "receipt_read_failed",
    "receipt_verification_failed",
    "receipt_write_failed",
    "root_mount_forbidden",
    "runtimeerror",
    "telemetry_disabled",
    "timeoutexpired",
    "unexpected_mount_path",
    "unknown_error",
    "valueerror",
}
_PG_DUMP_VERSION_CODE = re.compile(
    r"^pg_dump_version_mismatch_server_[0-9]+_client_[0-9]+$"
)


class BackupRecoveryError(RuntimeError):
    """Fail-closed backup receipt error with a non-sensitive reason code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class BackupRecoveryReceipt:
    receipt: str
    latest_outcome: str
    age_seconds: int
    max_age_hours: int

    def as_dict(self) -> dict[str, str | int]:
        return {
            "receipt": self.receipt,
            "latest_outcome": self.latest_outcome,
            "age_seconds": self.age_seconds,
            "max_age_hours": self.max_age_hours,
        }


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        return current.replace(tzinfo=UTC)
    return current.astimezone(UTC)


def _as_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _database_url(value: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned.startswith(("postgresql://", "postgres://")):
        raise BackupRecoveryError("database_url_invalid")
    return cleaned


def _bounded_hours(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = _DEFAULT_MAX_AGE_HOURS
    return max(1, min(parsed, 72))


def backup_recovery_max_age_hours(
    environment: Mapping[str, str] | None = None,
) -> int:
    value = (
        os.getenv("BACKUP_RECOVERY_MAX_AGE_HOURS", str(_DEFAULT_MAX_AGE_HOURS))
        if environment is None
        else environment.get("BACKUP_RECOVERY_MAX_AGE_HOURS", str(_DEFAULT_MAX_AGE_HOURS))
    )
    return _bounded_hours(value)


def production_backup_restore_certified(
    environment: Mapping[str, str] | None = None,
) -> bool:
    value = (
        os.getenv("PRODUCTION_BACKUP_RESTORE_CERTIFIED", "false")
        if environment is None
        else environment.get("PRODUCTION_BACKUP_RESTORE_CERTIFIED", "false")
    )
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _known_failure_code(value: Any) -> str:
    clean = str(value or "").strip().casefold()
    if clean in _KNOWN_FAILURE_CODES or _PG_DUMP_VERSION_CODE.fullmatch(clean):
        return clean
    return ""


def safe_backup_error_code(exc: Exception) -> str:
    """Return a bounded operational category without reflecting exception details."""
    identity = (type(exc).__module__, type(exc).__name__)
    if identity in _INTERNAL_CODE_TYPES:
        internal_code = _known_failure_code(getattr(exc, "code", ""))
        if internal_code:
            return internal_code

    generated_code = _known_failure_code(str(exc or ""))
    if generated_code:
        return generated_code

    name = re.sub(r"[^a-z0-9_.-]", "", type(exc).__name__.casefold())[:80]
    return name or "unknown_error"


def _latest_json_event(store: Any) -> tuple[str, datetime] | None:
    snapshot = store.snapshot()
    raw_events = snapshot.get("provider_events", []) if isinstance(snapshot, dict) else []
    matches: list[tuple[str, datetime]] = []
    for raw in raw_events if isinstance(raw_events, list) else []:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("provider") or "") != _BACKUP_PROVIDER:
            continue
        if str(raw.get("operation") or "") != _BACKUP_OPERATION:
            continue
        created = _as_datetime(raw.get("created_at"))
        if created is None:
            continue
        matches.append((str(raw.get("outcome") or "unknown"), created))
    return max(matches, key=lambda item: item[1]) if matches else None


def _latest_postgres_event(store: Any) -> tuple[str, datetime] | None:
    with store.pool.connection() as connection:
        row = connection.execute(
            """
            SELECT outcome, created_at
            FROM provider_operational_events
            WHERE provider = %s AND operation = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (_BACKUP_PROVIDER, _BACKUP_OPERATION),
        ).fetchone()
    if not row:
        return None
    created = _as_datetime(row.get("created_at"))
    if created is None:
        return (str(row.get("outcome") or "unknown"), datetime.min.replace(tzinfo=UTC))
    return str(row.get("outcome") or "unknown"), created


def _latest_event(store: Any) -> tuple[str, datetime] | None:
    backend = str(getattr(store, "backend_name", "json"))
    if backend == "postgresql":
        return _latest_postgres_event(store)
    return _latest_json_event(store)


def build_backup_recovery_metrics(
    store: Any,
    *,
    now: datetime | None = None,
    max_age_hours: int | None = None,
) -> dict[str, str | int]:
    """Return only bounded backup receipt state and age for launch decisions."""
    current = _now(now)
    maximum = _bounded_hours(
        backup_recovery_max_age_hours() if max_age_hours is None else max_age_hours
    )
    try:
        latest = _latest_event(store)
    except Exception as exc:
        raise BackupRecoveryError("receipt_read_failed") from exc
    if latest is None:
        return BackupRecoveryReceipt("missing", "none", 0, maximum).as_dict()

    outcome, created = latest
    if created == datetime.min.replace(tzinfo=UTC):
        return BackupRecoveryReceipt("invalid_timestamp", outcome[:30], 0, maximum).as_dict()

    raw_age = int((current - created).total_seconds())
    if raw_age < -_FUTURE_TOLERANCE_SECONDS:
        return BackupRecoveryReceipt("future_timestamp", outcome[:30], 0, maximum).as_dict()
    age = max(0, raw_age)
    recent = age <= maximum * 3600
    clean_outcome = outcome if outcome in _ALLOWED_OUTCOMES else "unknown"
    if recent and clean_outcome == "success":
        receipt = "recent_success"
    elif recent and clean_outcome == "started":
        receipt = "latest_started"
    elif recent and clean_outcome == "failure":
        receipt = "latest_failure"
    elif recent:
        receipt = "latest_unknown"
    elif clean_outcome == "success":
        receipt = "stale_success"
    elif clean_outcome == "started":
        receipt = "stale_started"
    elif clean_outcome == "failure":
        receipt = "stale_failure"
    else:
        receipt = "stale_unknown"
    return BackupRecoveryReceipt(receipt, clean_outcome, age, maximum).as_dict()


def record_backup_event(
    database_url: str,
    outcome: str,
    *,
    latency_ms: int = 0,
    error_code: str = "",
    now: datetime | None = None,
) -> dict[str, str | int]:
    """Persist and verify one operational backup attempt receipt."""
    clean_outcome = str(outcome or "").strip().casefold()
    if clean_outcome not in _ALLOWED_OUTCOMES:
        raise BackupRecoveryError("outcome_invalid")
    if not telemetry_enabled():
        raise BackupRecoveryError("telemetry_disabled")

    clean_error_code = ""
    if clean_outcome == "failure":
        clean_error_code = _known_failure_code(error_code) or "unknown_error"

    store: PostgresDataStore | None = None
    current = _now(now)
    try:
        store = PostgresDataStore(_database_url(database_url))
        repository = ProviderReliabilityRepository(store)
        repository.record(
            _BACKUP_PROVIDER,
            _BACKUP_OPERATION,
            clean_outcome,
            max(0, min(int(latency_ms), 1_800_000)),
            error_code=clean_error_code,
            now=current,
        )
        metrics = build_backup_recovery_metrics(
            store,
            now=current,
            max_age_hours=1,
        )
        if metrics.get("latest_outcome") != clean_outcome:
            raise BackupRecoveryError("receipt_verification_failed")
        return metrics
    except BackupRecoveryError:
        raise
    except Exception as exc:
        raise BackupRecoveryError("receipt_write_failed") from exc
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:
                pass
