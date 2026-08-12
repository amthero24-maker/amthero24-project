"""Tests for the strict production release gate."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import production_smoke
from schema_recovery import expected_schema_identity
from scripts import release_preflight


SCHEMA_IDENTITY = expected_schema_identity()


def _schema_fields() -> dict[str, object]:
    return SCHEMA_IDENTITY.manifest_fields()


def _passing_smoke() -> list[production_smoke.SmokeCheck]:
    return [
        production_smoke.SmokeCheck("health", "pass", "ok"),
        production_smoke.SmokeCheck("launch_decision", "pass", "ready"),
    ]


def test_gate_requires_explicit_release_identity() -> None:
    checks = release_preflight.run_gate(base_url="", admin_token="", expected_version="")
    assert {item.name for item in checks if not item.passed} == {"base_url", "admin_token", "expected_version"}


def test_gate_requires_every_strict_smoke_check() -> None:
    smoke = [
        production_smoke.SmokeCheck("health", "pass", "ok"),
        production_smoke.SmokeCheck("launch_decision", "fail", "warning"),
    ]
    with patch("scripts.release_preflight.production_smoke.run_smoke", return_value=smoke) as run:
        checks = release_preflight.run_gate(
            base_url="https://example.test",
            admin_token="secret",
            expected_version="3.3.0",
        )
    assert next(item for item in checks if item.name == "smoke_launch_decision").passed is False
    assert run.call_args.kwargs["require_postgresql"] is True
    assert run.call_args.kwargs["require_signature"] is True
    assert run.call_args.kwargs["require_launch_ready"] is True


def test_gate_requires_recent_backup_manifest_by_default() -> None:
    with patch(
        "scripts.release_preflight.production_smoke.run_smoke",
        return_value=_passing_smoke(),
    ):
        checks = release_preflight.run_gate(
            base_url="https://example.test",
            admin_token="secret",
            expected_version="4.7.0",
        )

    backup = next(item for item in checks if item.name == "backup_manifest")
    assert backup.passed is False
    assert backup.detail == "manifest path is required"


def test_gate_rejects_disabled_backup_verification() -> None:
    with patch(
        "scripts.release_preflight.production_smoke.run_smoke",
        return_value=_passing_smoke(),
    ):
        checks = release_preflight.run_gate(
            base_url="https://example.test",
            admin_token="secret",
            expected_version="4.7.0",
            require_backup=False,
        )

    backup = next(item for item in checks if item.name == "backup_requirement")
    assert backup.passed is False
    assert backup.detail == "recent encrypted backup verification cannot be disabled"


def test_manual_release_workflow_cannot_disable_backup_requirement() -> None:
    workflow = Path(".github/workflows/release-preflight.yml").read_text(
        encoding="utf-8"
    )
    environment = Path(".env.example").read_text(encoding="utf-8")
    script = Path("scripts/release_preflight.py").read_text(encoding="utf-8")

    assert "require_recent_backup:" not in workflow
    assert 'RELEASE_REQUIRE_RECENT_BACKUP: "true"' in workflow
    assert "RELEASE_REQUIRE_RECENT_BACKUP=true" in environment
    assert 'os.getenv("RELEASE_REQUIRE_RECENT_BACKUP", "true")' in script


def test_recent_encrypted_schema_bound_backup_manifest_passes(tmp_path) -> None:
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    manifest = tmp_path / "backup.manifest.json"
    manifest.write_text(json.dumps({
        "created_at": (now - timedelta(hours=2)).isoformat(),
        "artifact": "amthero24.dump.fernet",
        "encrypted_sha256": "a" * 64,
        **_schema_fields(),
    }), encoding="utf-8")
    checks = release_preflight.verify_backup_manifest(str(manifest), now=now)
    assert all(item.passed for item in checks)
    assert {item.name for item in checks} >= {
        "backup_schema_version",
        "backup_schema_checksum",
        "backup_schema_ledger",
        "backup_schema_contract",
    }


def test_old_or_invalid_artifact_metadata_blocks_release_with_valid_schema(tmp_path) -> None:
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    manifest = tmp_path / "backup.manifest.json"
    manifest.write_text(json.dumps({
        "created_at": (now - timedelta(days=3)).isoformat(),
        "artifact": "plain.dump",
        "encrypted_sha256": "bad",
        **_schema_fields(),
    }), encoding="utf-8")
    failed = {item.name for item in release_preflight.verify_backup_manifest(str(manifest), now=now) if not item.passed}
    assert failed == {"backup_age", "backup_integrity_metadata", "backup_artifact_metadata"}


def test_missing_schema_identity_blocks_release(tmp_path) -> None:
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    manifest = tmp_path / "backup.manifest.json"
    manifest.write_text(json.dumps({
        "created_at": (now - timedelta(hours=1)).isoformat(),
        "artifact": "amthero24.dump.fernet",
        "artifact_sha256": "b" * 64,
    }), encoding="utf-8")

    failed = {item.name for item in release_preflight.verify_backup_manifest(str(manifest), now=now) if not item.passed}
    assert failed == {
        "backup_schema_version",
        "backup_schema_checksum",
        "backup_schema_ledger",
        "backup_schema_contract",
    }


def test_incompatible_schema_identity_blocks_release(tmp_path) -> None:
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    manifest = tmp_path / "backup.manifest.json"
    manifest.write_text(json.dumps({
        "created_at": (now - timedelta(hours=1)).isoformat(),
        "artifact": "amthero24.dump.fernet",
        "artifact_sha256": "c" * 64,
        "schema_version": 999,
        "schema_checksum": "d" * 64,
        "schema_ledger_entries": 999,
        "schema_contract": "invalid",
    }), encoding="utf-8")

    failed = {item.name for item in release_preflight.verify_backup_manifest(str(manifest), now=now) if not item.passed}
    assert failed == {
        "backup_schema_version",
        "backup_schema_checksum",
        "backup_schema_ledger",
        "backup_schema_contract",
    }
