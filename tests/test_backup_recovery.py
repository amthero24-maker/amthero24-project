"""Privacy-safe production backup receipt and launch-metric tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import backup_recovery
from backup_recovery import (
    BackupRecoveryError,
    build_backup_recovery_metrics,
    record_backup_event,
    safe_backup_error_code,
)
from data_store import JsonDataStore
from provider_reliability import ProviderReliabilityRepository


def _store(tmp_path, monkeypatch) -> JsonDataStore:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("PROVIDER_TELEMETRY_ENABLED", "true")
    return JsonDataStore(tmp_path / "store.json")


def _record(
    store: JsonDataStore,
    outcome: str,
    when: datetime,
    *,
    error_code: str = "",
) -> None:
    ProviderReliabilityRepository(store).record(
        "postgres_backup",
        "encrypted_backup",
        outcome,
        100,
        error_code=error_code,
        now=when,
    )


def test_missing_receipt_is_fail_closed(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path, monkeypatch)
    metrics = build_backup_recovery_metrics(
        store,
        now=datetime(2026, 8, 12, 12, tzinfo=UTC),
        max_age_hours=30,
    )

    assert metrics == {
        "receipt": "missing",
        "latest_outcome": "none",
        "age_seconds": 0,
        "max_age_hours": 30,
    }


def test_recent_success_is_ready_and_contains_no_operational_values(
    tmp_path,
    monkeypatch,
) -> None:
    store = _store(tmp_path, monkeypatch)
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)
    _record(store, "success", now - timedelta(hours=2))

    metrics = build_backup_recovery_metrics(
        store,
        now=now,
        max_age_hours=30,
    )

    assert metrics == {
        "receipt": "recent_success",
        "latest_outcome": "success",
        "age_seconds": 7200,
        "max_age_hours": 30,
    }
    encoded = str(metrics)
    assert "postgresql://" not in encoded
    assert "/backups" not in encoded
    assert "artifact" not in encoded


def test_latest_started_attempt_blocks_an_older_success(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path, monkeypatch)
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)
    _record(store, "success", now - timedelta(hours=3))
    _record(store, "started", now - timedelta(minutes=5))

    metrics = build_backup_recovery_metrics(store, now=now, max_age_hours=30)

    assert metrics["receipt"] == "latest_started"
    assert metrics["latest_outcome"] == "started"
    assert metrics["age_seconds"] == 300


def test_latest_failure_blocks_an_older_success(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path, monkeypatch)
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)
    _record(store, "success", now - timedelta(hours=3))
    _record(store, "failure", now - timedelta(minutes=2), error_code="pg_dump_failed")

    metrics = build_backup_recovery_metrics(store, now=now, max_age_hours=30)

    assert metrics["receipt"] == "latest_failure"
    assert metrics["latest_outcome"] == "failure"
    assert "pg_dump_failed" not in str(metrics)


def test_stale_success_is_not_launch_evidence(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path, monkeypatch)
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)
    _record(store, "success", now - timedelta(hours=31))

    metrics = build_backup_recovery_metrics(store, now=now, max_age_hours=30)

    assert metrics["receipt"] == "stale_success"
    assert metrics["age_seconds"] == 31 * 3600


def test_material_future_timestamp_is_invalid(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path, monkeypatch)
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)
    _record(store, "success", now + timedelta(minutes=10))

    metrics = build_backup_recovery_metrics(store, now=now, max_age_hours=30)

    assert metrics["receipt"] == "future_timestamp"
    assert metrics["age_seconds"] == 0


def test_max_age_is_bounded(monkeypatch) -> None:
    assert backup_recovery.backup_recovery_max_age_hours(
        {"BACKUP_RECOVERY_MAX_AGE_HOURS": "0"}
    ) == 1
    assert backup_recovery.backup_recovery_max_age_hours(
        {"BACKUP_RECOVERY_MAX_AGE_HOURS": "999"}
    ) == 72
    assert backup_recovery.backup_recovery_max_age_hours(
        {"BACKUP_RECOVERY_MAX_AGE_HOURS": "invalid"}
    ) == 30


def test_record_backup_event_persists_and_verifies_receipt(
    tmp_path,
    monkeypatch,
) -> None:
    store = _store(tmp_path, monkeypatch)
    monkeypatch.setattr(backup_recovery, "PostgresDataStore", lambda _url: store)
    now = datetime(2026, 8, 12, 12, tzinfo=UTC)

    metrics = record_backup_event(
        "postgresql://synthetic.invalid/database",
        "success",
        latency_ms=321,
        now=now,
    )

    assert metrics["receipt"] == "recent_success"
    assert metrics["latest_outcome"] == "success"
    event = store.snapshot()["provider_events"][-1]
    assert event["provider"] == "postgres_backup"
    assert event["operation"] == "encrypted_backup"
    assert event["outcome"] == "success"
    assert "synthetic.invalid" not in str(event)


def test_record_backup_event_rejects_disabled_telemetry(
    tmp_path,
    monkeypatch,
) -> None:
    store = _store(tmp_path, monkeypatch)
    monkeypatch.setattr(backup_recovery, "PostgresDataStore", lambda _url: store)
    monkeypatch.setenv("PROVIDER_TELEMETRY_ENABLED", "false")

    with pytest.raises(BackupRecoveryError) as raised:
        record_backup_event(
            "postgresql://synthetic.invalid/database",
            "started",
        )

    assert raised.value.code == "telemetry_disabled"
    assert store.snapshot().get("provider_events", []) == []


def test_record_backup_event_rejects_unknown_outcome(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path, monkeypatch)
    monkeypatch.setattr(backup_recovery, "PostgresDataStore", lambda _url: store)

    with pytest.raises(BackupRecoveryError) as raised:
        record_backup_event(
            "postgresql://synthetic.invalid/database",
            "maybe",
        )

    assert raised.value.code == "outcome_invalid"


def test_error_sanitizer_never_reflects_private_exception_text() -> None:
    error = RuntimeError(
        "https://private.invalid/path?token=secret /backups/customer.dump"
    )

    assert safe_backup_error_code(error) == "runtimeerror"
    assert "private.invalid" not in safe_backup_error_code(error)
    assert "customer.dump" not in safe_backup_error_code(error)
