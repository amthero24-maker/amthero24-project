"""Versioned PostgreSQL schema migrations with bounded cross-replica locking.

The migration ledger stores only schema metadata: integer version, migration name,
checksum, application version, and timestamps. It never receives or stores user data,
message content, phone hashes, provider payloads, or credentials.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable


class SchemaMigrationError(RuntimeError):
    """Fail-closed migration error identified by a non-sensitive operational code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class MigrationReport:
    status: str
    current_version: int
    required_version: int
    applied_versions: tuple[int, ...]
    components: tuple[str, ...]
    schema_checksum: str


@dataclass(frozen=True)
class MigrationSpec:
    version: int
    name: str
    checksum: str
    apply: Callable[[Any, Any], tuple[str, ...]]


# Stable signed bigint used only for pg advisory locking. It is not a secret.
_MIGRATION_LOCK_KEY = 4_814_172_024_045
_LEDGER_TABLE = "amthero_schema_migrations"

_EXPECTED_SCHEMA: dict[str, tuple[str, ...]] = {
    "hero_users": ("phone_hash", "profile"),
    "inbound_messages": ("message_id", "phone_hash", "status"),
    "inbound_work_queue": ("message_id", "status", "lease_owner"),
    "outbound_delivery_messages": ("message_hash", "status"),
    "schema_migrations": ("name", "applied_at"),
    "hero_missions": ("mission_id", "phone_hash", "status"),
    "memory_consent_events": ("event_id", "phone_hash"),
    "hero_reminders": ("reminder_id", "status", "lease_owner"),
    "pending_document_actions": ("action_id", "phone_hash"),
    "hero_entitlements": ("phone_hash", "plan"),
    "hero_usage_counters": ("phone_hash", "period_key"),
    "abuse_rate_windows": ("phone_hash", "window_key"),
    "abuse_blocks": ("phone_hash", "blocked_until"),
    "abuse_guard_events": ("event_id", "occurred_at"),
    "provider_operational_events": ("event_id", "provider"),
    "provider_circuit_state": ("provider", "operation"),
    "human_support_tickets": ("ticket_id", "phone_hash"),
    "human_support_admin_events": ("event_id", "ticket_id"),
    "anonymous_feedback": ("feedback_id", "rating"),
    _LEDGER_TABLE: ("version", "name", "checksum", "app_version", "applied_at"),
}


def _bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)).strip())
    except ValueError:
        value = default
    return min(max(value, minimum), maximum)


def migration_lock_timeout_seconds() -> float:
    return _bounded_float("SCHEMA_MIGRATION_LOCK_TIMEOUT_SECONDS", 30.0, 1.0, 120.0)


def migration_lock_key() -> int:
    return _MIGRATION_LOCK_KEY


def _contract_checksum(version: int, name: str, schema: dict[str, tuple[str, ...]]) -> str:
    payload = {
        "version": int(version),
        "name": str(name),
        "schema": {table: list(columns) for table, columns in sorted(schema.items())},
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _ensure_ledger(connection: Any) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_LEDGER_TABLE} (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            checksum TEXT NOT NULL,
            app_version TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )


def _acquire_lock(connection: Any, timeout: float) -> None:
    deadline = time.monotonic() + max(1.0, float(timeout))
    while True:
        row = connection.execute(
            "SELECT pg_try_advisory_lock(%s) AS acquired",
            (_MIGRATION_LOCK_KEY,),
        ).fetchone()
        acquired = bool(row and (row.get("acquired") if hasattr(row, "get") else row[0]))
        if acquired:
            return
        if time.monotonic() >= deadline:
            raise SchemaMigrationError("migration_lock_timeout")
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


def _release_lock(connection: Any) -> None:
    try:
        connection.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK_KEY,))
    except Exception:
        # The connection closing also releases a session advisory lock. Never mask the
        # original migration outcome with a secondary unlock failure.
        pass


def _apply_schema_v1(store: Any, connection: Any) -> tuple[str, ...]:
    # Core tables are normally initialized by PostgresDataStore. Production creates the
    # pool without DDL and reaches this function while holding the migration lock.
    store._initialize_schema()

    from schema_bootstrap import bootstrap_postgres_schemas

    components = bootstrap_postgres_schemas(store)
    connection.execute(
        "ALTER TABLE inbound_work_queue ADD COLUMN IF NOT EXISTS lease_owner TEXT"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS inbound_work_queue_owner_idx "
        "ON inbound_work_queue (lease_owner) WHERE status = 'processing'"
    )
    connection.execute(
        "ALTER TABLE hero_reminders ADD COLUMN IF NOT EXISTS lease_owner TEXT"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS hero_reminders_owner_idx "
        "ON hero_reminders (lease_owner) WHERE status = 'processing'"
    )
    return components


_SCHEMA_V1_NAME = "production_schema_v1"
_SCHEMA_V1_CHECKSUM = _contract_checksum(1, _SCHEMA_V1_NAME, _EXPECTED_SCHEMA)
_MIGRATIONS = (
    MigrationSpec(1, _SCHEMA_V1_NAME, _SCHEMA_V1_CHECKSUM, _apply_schema_v1),
)
LATEST_SCHEMA_VERSION = _MIGRATIONS[-1].version


def _applied_rows(connection: Any) -> dict[int, dict[str, Any]]:
    rows = connection.execute(
        f"SELECT version, name, checksum, app_version, applied_at FROM {_LEDGER_TABLE} ORDER BY version"
    ).fetchall()
    return {int(row["version"]): dict(row) for row in rows}


def validate_schema_contract(connection: Any) -> tuple[bool, tuple[str, ...]]:
    """Validate required public-schema tables and columns without reading application rows."""
    rows = connection.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = current_schema()
        """
    ).fetchall()
    available: dict[str, set[str]] = {}
    for row in rows:
        available.setdefault(str(row["table_name"]), set()).add(str(row["column_name"]))

    missing: list[str] = []
    for table, columns in _EXPECTED_SCHEMA.items():
        present = available.get(table)
        if present is None:
            missing.append(f"table:{table}")
            continue
        for column in columns:
            if column not in present:
                missing.append(f"column:{table}.{column}")
    return not missing, tuple(sorted(missing))


def run_database_migrations(store: Any, *, app_version: str) -> MigrationReport:
    """Apply ordered migrations under one bounded PostgreSQL advisory lock."""
    if str(getattr(store, "backend_name", "json")) != "postgresql":
        return MigrationReport("not-applicable", 0, LATEST_SCHEMA_VERSION, (), (), "")

    applied_now: list[int] = []
    components: tuple[str, ...] = ()
    with store.pool.connection() as connection:
        _acquire_lock(connection, migration_lock_timeout_seconds())
        try:
            _ensure_ledger(connection)
            applied = _applied_rows(connection)
            if applied and max(applied) > LATEST_SCHEMA_VERSION:
                raise SchemaMigrationError("database_schema_ahead")

            for migration in _MIGRATIONS:
                existing = applied.get(migration.version)
                if existing:
                    if str(existing.get("name") or "") != migration.name:
                        raise SchemaMigrationError("migration_name_mismatch")
                    if str(existing.get("checksum") or "") != migration.checksum:
                        raise SchemaMigrationError("migration_checksum_mismatch")
                    continue

                components = migration.apply(store, connection)
                connection.execute(
                    f"""
                    INSERT INTO {_LEDGER_TABLE} (version, name, checksum, app_version)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (migration.version, migration.name, migration.checksum, str(app_version)[:40]),
                )
                applied_now.append(migration.version)

            valid, _missing = validate_schema_contract(connection)
            if not valid:
                raise SchemaMigrationError("schema_contract_incomplete")
        finally:
            _release_lock(connection)

    if not components:
        # Existing databases still need the component list for readiness; it contains only
        # static subsystem names and no database contents.
        from schema_bootstrap import schema_component_names

        components = schema_component_names()

    report = MigrationReport(
        status="current",
        current_version=LATEST_SCHEMA_VERSION,
        required_version=LATEST_SCHEMA_VERSION,
        applied_versions=tuple(applied_now),
        components=tuple(components),
        schema_checksum=_SCHEMA_V1_CHECKSUM,
    )
    setattr(store, "schema_migration_report", report)
    setattr(store, "schema_bootstrapped_components", report.components)
    return report


def migration_readiness(store: Any) -> tuple[str, int]:
    if str(getattr(store, "backend_name", "json")) != "postgresql":
        return "not-applicable", 0
    report = getattr(store, "schema_migration_report", None)
    if isinstance(report, MigrationReport) and report.status == "current":
        return "current", int(report.current_version)
    return "unverified", 0
