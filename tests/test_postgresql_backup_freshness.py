"""Real PostgreSQL tests for privacy-safe backup freshness operations."""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

import admin_extensions
import launch_extensions
import runtime_health
from backup_freshness import (
    BackupFreshnessError,
    aggregate_backup_freshness,
    record_backup_failure,
    record_backup_success,
)
from database_migrations import LATEST_SCHEMA_VERSION
from schema_recovery import expected_schema_identity


@pytest.fixture(autouse=True)
def clean_backup_state() -> None:
    store = runtime_health.store
    assert store.backend_name == "postgresql"
    with store.pool.connection() as connection:
        connection.execute("TRUNCATE backup_operational_state")
    yield
    with store.pool.connection() as connection:
        connection.execute("TRUNCATE backup_operational_state")


def _manifest() -> dict[str, object]:
    identity = expected_schema_identity()
    return {
        "encrypted": True,
        "artifact_sha256": "a" * 64,
        "artifact_size_bytes": 8192,
        **identity.manifest_fields(),
    }


def test_success_checkpoint_is_visible_as_aggregate_only() -> None:
    now = datetime(2026, 7, 27, 8, tzinfo=UTC)
    record_backup_success(os.environ["DATABASE_URL"], _manifest(), now=now)

    payload = aggregate_backup_freshness(runtime_health.store, now=now + timedelta(hours=4))
    assert payload == {
        "state": "recorded",
        "last_status": "success",
        "last_success_at": now.isoformat(),
        "last_attempt_at": now.isoformat(),
        "age_hours": 4.0,
        "encrypted": True,
        "schema_version": LATEST_SCHEMA_VERSION,
        "artifact_size_bytes": 8192,
        "last_failure_code": "",
    }

    overview = admin_extensions.build_overview(runtime_health.store, now=now + timedelta(hours=4))
    assert overview["backups"] == payload
    encoded = str(overview["backups"]).casefold()
    assert "artifact_sha256" not in encoded
    assert "filename" not in encoded
    assert "database_url" not in encoded


def test_failed_attempt_preserves_last_success_and_warns_launch() -> None:
    success_at = datetime(2026, 7, 27, 8, tzinfo=UTC)
    failure_at = success_at + timedelta(hours=2)
    record_backup_success(os.environ["DATABASE_URL"], _manifest(), now=success_at)
    record_backup_failure(
        os.environ["DATABASE_URL"],
        "pg_dump_connection_failed",
        now=failure_at,
    )

    overview = admin_extensions.build_overview(
        runtime_health.store,
        now=failure_at + timedelta(hours=1),
    )
    backup = overview["backups"]
    assert backup["last_status"] == "failed"
    assert backup["last_success_at"] == success_at.isoformat()
    assert backup["last_attempt_at"] == failure_at.isoformat()
    assert backup["age_hours"] == 3.0
    assert backup["last_failure_code"] == "pg_dump_connection_failed"

    report = launch_extensions.build_launch_report(
        overview,
        now=failure_at + timedelta(hours=1),
        env={
            "BACKUP_FRESHNESS_ENFORCEMENT_ENABLED": "true",
            "BACKUP_WARNING_AFTER_HOURS": "30",
            "BACKUP_BLOCK_AFTER_HOURS": "48",
        },
    )
    check = next(item for item in report["checks"] if item["code"] == "backup_freshness")
    assert check["status"] == "warning"


def test_stale_checkpoint_blocks_when_enforcement_is_enabled() -> None:
    success_at = datetime(2026, 7, 25, 0, tzinfo=UTC)
    now = success_at + timedelta(hours=60)
    record_backup_success(os.environ["DATABASE_URL"], _manifest(), now=success_at)
    overview = admin_extensions.build_overview(runtime_health.store, now=now)

    report = launch_extensions.build_launch_report(
        overview,
        now=now,
        env={
            "BACKUP_FRESHNESS_ENFORCEMENT_ENABLED": "true",
            "BACKUP_WARNING_AFTER_HOURS": "30",
            "BACKUP_BLOCK_AFTER_HOURS": "48",
        },
    )
    check = next(item for item in report["checks"] if item["code"] == "backup_freshness")
    assert check["status"] == "blocked"
    assert report["status"] == "blocked"


def test_invalid_manifest_is_rejected_without_checkpoint_row() -> None:
    manifest = _manifest()
    manifest["encrypted"] = False
    with pytest.raises(BackupFreshnessError) as raised:
        record_backup_success(os.environ["DATABASE_URL"], manifest)
    assert raised.value.code == "backup_artifact_unencrypted"

    with runtime_health.store.pool.connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS count FROM backup_operational_state"
        ).fetchone()
    assert int(count["count"]) == 0


def test_table_schema_contains_no_user_or_artifact_location_columns() -> None:
    with runtime_health.store.pool.connection() as connection:
        rows = connection.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'backup_operational_state'
            ORDER BY column_name
            """
        ).fetchall()
    columns = {str(row["column_name"]) for row in rows}
    assert columns == {
        "artifact_sha256",
        "artifact_size_bytes",
        "encrypted",
        "last_attempt_at",
        "last_failure_at",
        "last_failure_code",
        "last_status",
        "last_success_at",
        "schema_checksum",
        "schema_version",
        "scope",
        "updated_at",
    }
    forbidden = {
        "filename",
        "filepath",
        "database_url",
        "phone_hash",
        "message_id",
        "user_id",
        "ciphertext",
        "token",
    }
    assert not columns & forbidden
