"""Tests for privacy-safe backup freshness state and launch policy."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from backup_freshness import aggregate_backup_freshness, safe_backup_failure_code
from backup_freshness_policy import backup_freshness_check, backup_thresholds
from database_migrations import LATEST_SCHEMA_VERSION


class _Store:
    backend_name = "postgresql"

    def __init__(self, row=None, *, raises: bool = False) -> None:
        connection = MagicMock()
        if raises:
            connection.execute.side_effect = RuntimeError("private database detail")
        else:
            connection.execute.return_value.fetchone.return_value = row
        context = MagicMock()
        context.__enter__.return_value = connection
        self.pool = MagicMock()
        self.pool.connection.return_value = context


def _overview(**updates):
    payload = {
        "state": "recorded",
        "last_status": "success",
        "last_success_at": "2026-07-27T10:00:00+00:00",
        "last_attempt_at": "2026-07-27T10:00:00+00:00",
        "age_hours": 4.0,
        "encrypted": True,
        "schema_version": LATEST_SCHEMA_VERSION,
        "artifact_size_bytes": 2048,
        "last_failure_code": "",
    }
    payload.update(updates)
    return {"backups": payload}


def test_thresholds_are_bounded_and_ordered() -> None:
    assert backup_thresholds({
        "BACKUP_WARNING_AFTER_HOURS": "0",
        "BACKUP_BLOCK_AFTER_HOURS": "1",
    }) == (1, 2)
    assert backup_thresholds({
        "BACKUP_WARNING_AFTER_HOURS": "999",
        "BACKUP_BLOCK_AFTER_HOURS": "999",
    }) == (168, 336)
    assert backup_thresholds({}) == (30, 48)


def test_missing_backup_warns_before_enforcement_and_blocks_after() -> None:
    overview = _overview(state="missing", age_hours=None, encrypted=False, schema_version=0)

    observed = backup_freshness_check(overview, environment={})
    enforced = backup_freshness_check(
        overview,
        environment={"BACKUP_FRESHNESS_ENFORCEMENT_ENABLED": "true"},
    )

    assert observed["status"] == "warning"
    assert enforced["status"] == "blocked"
    assert observed["code"] == enforced["code"] == "backup_freshness"


def test_fresh_encrypted_current_schema_is_ready() -> None:
    check = backup_freshness_check(_overview(), environment={})
    assert check["status"] == "ready"
    assert "4.0 hours" in check["detail"]


def test_warning_and_block_thresholds_use_age_only() -> None:
    warning = backup_freshness_check(
        _overview(age_hours=31.0),
        environment={"BACKUP_WARNING_AFTER_HOURS": "30", "BACKUP_BLOCK_AFTER_HOURS": "48"},
    )
    observed_block = backup_freshness_check(
        _overview(age_hours=49.0),
        environment={"BACKUP_WARNING_AFTER_HOURS": "30", "BACKUP_BLOCK_AFTER_HOURS": "48"},
    )
    enforced_block = backup_freshness_check(
        _overview(age_hours=49.0),
        environment={
            "BACKUP_WARNING_AFTER_HOURS": "30",
            "BACKUP_BLOCK_AFTER_HOURS": "48",
            "BACKUP_FRESHNESS_ENFORCEMENT_ENABLED": "true",
        },
    )

    assert warning["status"] == "warning"
    assert observed_block["status"] == "warning"
    assert enforced_block["status"] == "blocked"


def test_latest_failed_attempt_warns_while_last_success_is_fresh() -> None:
    check = backup_freshness_check(
        _overview(last_status="failed", last_failure_code="pg_dump_connection_failed"),
        environment={"BACKUP_FRESHNESS_ENFORCEMENT_ENABLED": "true"},
    )
    assert check["status"] == "warning"
    assert "latest backup attempt failed" in check["detail"]


def test_unencrypted_invalid_or_wrong_schema_always_blocks() -> None:
    assert backup_freshness_check(_overview(encrypted=False), environment={})["status"] == "blocked"
    assert backup_freshness_check(_overview(state="invalid"), environment={})["status"] == "blocked"
    assert backup_freshness_check(_overview(schema_version=LATEST_SCHEMA_VERSION - 1), environment={})["status"] == "blocked"


def test_aggregate_contains_no_artifact_identity_or_user_data() -> None:
    now = datetime(2026, 7, 27, 12, tzinfo=UTC)
    row = {
        "last_attempt_at": now - timedelta(hours=2),
        "last_success_at": now - timedelta(hours=3),
        "last_status": "failed",
        "last_failure_code": "pg_dump_connection_failed",
        "artifact_size_bytes": 4096,
        "schema_version": LATEST_SCHEMA_VERSION,
        "encrypted": True,
    }
    payload = aggregate_backup_freshness(_Store(row), now=now)

    assert payload["state"] == "recorded"
    assert payload["age_hours"] == 3.0
    assert payload["last_status"] == "failed"
    assert set(payload) == {
        "state",
        "last_status",
        "last_success_at",
        "last_attempt_at",
        "age_hours",
        "encrypted",
        "schema_version",
        "artifact_size_bytes",
        "last_failure_code",
    }
    encoded = str(payload).casefold()
    for forbidden in (
        "artifact_sha256",
        "filename",
        "filepath",
        "database_url",
        "phone",
        "message",
        "document",
        "ciphertext",
        "token",
    ):
        assert forbidden not in encoded


def test_missing_and_read_failure_are_safe_aggregate_states() -> None:
    assert aggregate_backup_freshness(_Store())["state"] == "missing"
    failure = aggregate_backup_freshness(_Store(raises=True))
    assert failure["state"] == "unavailable"
    assert failure["last_failure_code"] == "backup_checkpoint_read_failed"
    assert "private database detail" not in str(failure)


def test_failure_code_mapping_never_returns_exception_message() -> None:
    code = safe_backup_failure_code(RuntimeError("postgresql://private.invalid/db?token=value"))
    assert code == "backup_runtime_failed"
    assert "private.invalid" not in code
    assert "token" not in code
