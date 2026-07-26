"""Tests for backup CLI success/failure checkpoint behavior."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from backup_freshness import BackupFreshnessError
from database_migrations import LATEST_SCHEMA_VERSION
from scripts import postgres_backup


DATABASE_URL = "postgresql://db.internal/amthero24_test"


def _result(tmp_path: Path):
    artifact = tmp_path / "amthero24-test.dump.fernet"
    manifest = tmp_path / "amthero24-test.dump.fernet.manifest.json"
    metadata = {
        "encrypted": True,
        "artifact_size_bytes": 4096,
        "schema_version": LATEST_SCHEMA_VERSION,
    }
    return artifact, manifest, metadata


def test_cli_records_success_only_after_artifact_creation(tmp_path, capsys) -> None:
    result = _result(tmp_path)
    with patch("scripts.postgres_backup.create_backup", return_value=result) as create, patch(
        "scripts.postgres_backup.record_backup_success"
    ) as record:
        status = postgres_backup.main([
            "--database-url", DATABASE_URL,
            "--output-dir", str(tmp_path),
            "--encryption-key", "runtime-only-placeholder",
        ])

    assert status == 0
    create.assert_called_once()
    record.assert_called_once_with(DATABASE_URL, result[2])
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "status": "created_and_recorded",
        "artifact": result[0].name,
        "manifest": result[1].name,
        "size_bytes": 4096,
        "schema_version": LATEST_SCHEMA_VERSION,
    }
    assert str(tmp_path) not in json.dumps(payload)


def test_cli_records_safe_failure_code_without_exception_message(tmp_path, capsys) -> None:
    private_detail = "postgresql://private.invalid/data?token=hidden"
    with patch(
        "scripts.postgres_backup.create_backup",
        side_effect=RuntimeError(private_detail),
    ), patch("scripts.postgres_backup.record_backup_failure") as record:
        status = postgres_backup.main([
            "--database-url", DATABASE_URL,
            "--output-dir", str(tmp_path),
            "--encryption-key", "runtime-only-placeholder",
        ])

    assert status == 1
    record.assert_called_once_with(DATABASE_URL, "backup_runtime_failed")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "Backup failed: backup_runtime_failed"
    assert "private.invalid" not in captured.err
    assert "token" not in captured.err


def test_cli_fails_when_success_checkpoint_cannot_be_written(tmp_path, capsys) -> None:
    result = _result(tmp_path)
    with patch("scripts.postgres_backup.create_backup", return_value=result), patch(
        "scripts.postgres_backup.record_backup_success",
        side_effect=BackupFreshnessError("backup_checkpoint_write_failed"),
    ):
        status = postgres_backup.main([
            "--database-url", DATABASE_URL,
            "--output-dir", str(tmp_path),
            "--encryption-key", "runtime-only-placeholder",
        ])

    assert status == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "Backup failed: backup_checkpoint_write_failed"


def test_unencrypted_explicit_backup_stays_unmonitored(tmp_path, capsys) -> None:
    artifact = tmp_path / "amthero24-test.dump"
    manifest = tmp_path / "amthero24-test.dump.manifest.json"
    metadata = {
        "encrypted": False,
        "artifact_size_bytes": 1024,
        "schema_version": LATEST_SCHEMA_VERSION,
    }
    with patch(
        "scripts.postgres_backup.create_backup",
        return_value=(artifact, manifest, metadata),
    ), patch("scripts.postgres_backup.record_backup_success") as record:
        status = postgres_backup.main([
            "--database-url", DATABASE_URL,
            "--output-dir", str(tmp_path),
            "--allow-unencrypted",
        ])

    assert status == 0
    record.assert_not_called()
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "created_unmonitored"
