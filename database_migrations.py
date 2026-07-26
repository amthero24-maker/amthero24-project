"""Versioned PostgreSQL schema migrations with immutable historical checksums.

Each migration checksum is bound only to that migration's frozen contract. Extending the
current schema therefore never rewrites history or invalidates an already-applied ledger
entry. Startup migrations remain expand-only and pass through the online-safe executor.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable

from migration_policy import require_online_safe_sql


class SchemaMigrationError(RuntimeError):
    """Fail-closed migration error identified by a non-sensitive operational code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class OnlineMigrationExecutor:
    """Execute one literal expand-only DDL statement after runtime policy validation."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def execute(self, statement: str, params: Any | None = None) -> Any:
        try:
            require_online_safe_sql(statement)
        except ValueError as exc:
            raise SchemaMigrationError("unsafe_online_migration_sql") from exc
        if params is None:
            return self.connection.execute(statement)
        return self.connection.execute(statement, params)


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
    apply: Callable[[Any, OnlineMigrationExecutor], tuple[str, ...]]
    phase: str
    legacy_bootstrap: bool = False


_MIGRATION_LOCK_KEY = 4_814_172_024_045
_LEDGER_TABLE = "amthero_schema_migrations"
_BACKUP_STATE_TABLE = "backup_operational_state"

# This exact mapping produced the version-1 checksum deployed in AmtHero24 v4.5-v4.7.
# Never add a future table or column to this frozen historical contract.
_SCHEMA_V1_CONTRACT: dict[str, tuple[str, ...]] = {
    "hero_users": ("phone_hash", "profile"),
    "inbound_messages": ("message_id", "phone_hash", "status"),
    "inbound_work_queue": ("message_id", "status", "lease_owner"),
    "outbound_delivery_messages": ("message_hash", "status"),
    "schema_migrations": ("name", "applied_at"),
    "hero_missions": ("mission_id", "phone_hash", "status"),
    "memory_consent_events": ("event_id", "phone_hash"),
    "hero_reminders": ("reminder_id", "status", "lease_owner"),
    "pending_document_actions": (),
    "hero_entitlements": (),
    "hero_usage_counters": (),
    "abuse_rate_windows": (),
    "abuse_blocks": (),
    "abuse_guard_events": (),
    "provider_operational_events": (),
    "provider_circuit_state": (),
    "human_support_tickets": (),
    "human_support_admin_events": (),
    "anonymous_feedback": (),
    _LEDGER_TABLE: ("version", "name", "checksum", "app_version", "applied_at"),
}

_SCHEMA_V2_CONTRACT: dict[str, tuple[str, ...]] = {
    _BACKUP_STATE_TABLE: (
        "scope",
        "last_attempt_at",
        "last_success_at",
        "last_failure_at",
        "last_status",
        "last_failure_code",
        "artifact_sha256",
        "artifact_size_bytes",
        "schema_version",
        "schema_checksum",
        "encrypted",
        "updated_at",
    ),
}

# The current runtime contract is a union. Historical checksums are never calculated from
# this mutable union.
_EXPECTED_SCHEMA: dict[str, tuple[str, ...]] = {
    **_SCHEMA_V1_CONTRACT,
    **_SCHEMA_V2_CONTRACT,
}


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)).strip())
    except ValueError:
        value = default
    return min(max(value, minimum), maximum)


def migration_lock_timeout_seconds() -> float:
    return _env_float("SCHEMA_MIGRATION_LOCK_TIMEOUT_SECONDS", 30.0, 1.0, 120.0)


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
        pass


def _apply_schema_v1(store: Any, executor: OnlineMigrationExecutor) -> tuple[str, ...]:
    # Version 1 records the legacy idempotent schema foundation. Future migrations must not
    # invoke repository bootstrap and must express all SQL through this executor.
    store._initialize_schema()

    from schema_bootstrap import bootstrap_postgres_schemas

    components = bootstrap_postgres_schemas(store)
    executor.execute(
        "ALTER TABLE inbound_work_queue ADD COLUMN IF NOT EXISTS lease_owner TEXT"
    )
    executor.execute(
        "CREATE INDEX IF NOT EXISTS inbound_work_queue_owner_idx "
        "ON inbound_work_queue (lease_owner) WHERE status = 'processing'"
    )
    executor.execute(
        "ALTER TABLE hero_reminders ADD COLUMN IF NOT EXISTS lease_owner TEXT"
    )
    executor.execute(
        "CREATE INDEX IF NOT EXISTS hero_reminders_owner_idx "
        "ON hero_reminders (lease_owner) WHERE status = 'processing'"
    )
    return components


def _apply_backup_freshness_v2(
    store: Any,
    executor: OnlineMigrationExecutor,
) -> tuple[str, ...]:
    executor.execute(
        """
        CREATE TABLE IF NOT EXISTS backup_operational_state (
            scope TEXT PRIMARY KEY,
            last_attempt_at TIMESTAMPTZ,
            last_success_at TIMESTAMPTZ,
            last_failure_at TIMESTAMPTZ,
            last_status TEXT NOT NULL DEFAULT 'never',
            last_failure_code TEXT NOT NULL DEFAULT '',
            artifact_sha256 TEXT NOT NULL DEFAULT '',
            artifact_size_bytes BIGINT NOT NULL DEFAULT 0,
            schema_version INTEGER NOT NULL DEFAULT 0,
            schema_checksum TEXT NOT NULL DEFAULT '',
            encrypted BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    return ("backup_freshness",)


_SCHEMA_V1_NAME = "production_schema_v1"
# Frozen literal from the contract deployed before migration version 2 existed.
_SCHEMA_V1_CHECKSUM = "b79ba86b0703b775ba29b6321c73ae9227f327f52cd53ff518a921e5f9b67c5a"
_SCHEMA_V2_NAME = "backup_freshness_v2"
_SCHEMA_V2_CHECKSUM = _contract_checksum(2, _SCHEMA_V2_NAME, _SCHEMA_V2_CONTRACT)

_MIGRATIONS = (
    MigrationSpec(
        version=1,
        name=_SCHEMA_V1_NAME,
        checksum=_SCHEMA_V1_CHECKSUM,
        apply=_apply_schema_v1,
        phase="expand",
        legacy_bootstrap=True,
    ),
    MigrationSpec(
        version=2,
        name=_SCHEMA_V2_NAME,
        checksum=_SCHEMA_V2_CHECKSUM,
        apply=_apply_backup_freshness_v2,
        phase="expand",
    ),
)
LATEST_SCHEMA_VERSION = _MIGRATIONS[-1].version


def _applied_rows(connection: Any) -> dict[int, dict[str, Any]]:
    rows = connection.execute(
        f"SELECT version, name, checksum, app_version, applied_at FROM {_LEDGER_TABLE} ORDER BY version"
    ).fetchall()
    return {int(row["version"]): dict(row) for row in rows}


def validate_schema_contract(connection: Any) -> tuple[bool, tuple[str, ...]]:
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


def _validate_registry() -> None:
    versions = [migration.version for migration in _MIGRATIONS]
    if versions != list(range(1, len(versions) + 1)):
        raise SchemaMigrationError("migration_version_sequence")
    names = [migration.name for migration in _MIGRATIONS]
    if len(names) != len(set(names)):
        raise SchemaMigrationError("migration_name_duplicate")
    for migration in _MIGRATIONS:
        if migration.phase != "expand":
            raise SchemaMigrationError("unsafe_migration_phase")
        if migration.legacy_bootstrap and migration.version != 1:
            raise SchemaMigrationError("unsafe_legacy_bootstrap")


def run_database_migrations(store: Any, *, app_version: str) -> MigrationReport:
    if str(getattr(store, "backend_name", "json")) != "postgresql":
        return MigrationReport("not-applicable", 0, LATEST_SCHEMA_VERSION, (), (), "")

    _validate_registry()
    applied_now: list[int] = []
    applied_components: list[str] = []
    with store.pool.connection() as connection:
        _acquire_lock(connection, migration_lock_timeout_seconds())
        try:
            _ensure_ledger(connection)
            applied = _applied_rows(connection)
            if applied and max(applied) > LATEST_SCHEMA_VERSION:
                raise SchemaMigrationError("database_schema_ahead")

            executor = OnlineMigrationExecutor(connection)
            for migration in _MIGRATIONS:
                existing = applied.get(migration.version)
                if existing:
                    if str(existing.get("name") or "") != migration.name:
                        raise SchemaMigrationError("migration_name_mismatch")
                    if str(existing.get("checksum") or "") != migration.checksum:
                        raise SchemaMigrationError("migration_checksum_mismatch")
                    continue

                applied_components.extend(migration.apply(store, executor))
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

    from schema_bootstrap import schema_component_names

    components = tuple(dict.fromkeys((*schema_component_names(), "backup_freshness", *applied_components)))
    report = MigrationReport(
        status="current",
        current_version=LATEST_SCHEMA_VERSION,
        required_version=LATEST_SCHEMA_VERSION,
        applied_versions=tuple(applied_now),
        components=components,
        schema_checksum=_MIGRATIONS[-1].checksum,
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
