"""Tests for the strict production release gate."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import production_smoke
from scripts import release_preflight


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


def test_recent_encrypted_backup_manifest_passes(tmp_path) -> None:
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    manifest = tmp_path / "backup.manifest.json"
    manifest.write_text(json.dumps({
        "created_at": (now - timedelta(hours=2)).isoformat(),
        "artifact": "amthero24.dump.fernet",
        "encrypted_sha256": "a" * 64,
    }), encoding="utf-8")
    checks = release_preflight.verify_backup_manifest(str(manifest), now=now)
    assert all(item.passed for item in checks)


def test_old_or_invalid_backup_manifest_blocks_release(tmp_path) -> None:
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    manifest = tmp_path / "backup.manifest.json"
    manifest.write_text(json.dumps({
        "created_at": (now - timedelta(days=3)).isoformat(),
        "artifact": "plain.dump",
        "encrypted_sha256": "bad",
    }), encoding="utf-8")
    failed = {item.name for item in release_preflight.verify_backup_manifest(str(manifest), now=now) if not item.passed}
    assert failed == {"backup_age", "backup_integrity_metadata", "backup_artifact_metadata"}
