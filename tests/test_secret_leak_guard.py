"""Tests for the privacy-safe repository credential scanner."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.scan_repository_secrets import scan_repository, scan_text


def test_repository_has_no_detected_credentials() -> None:
    root = Path(__file__).resolve().parents[1]
    assert scan_repository(root) == []


def test_scanner_detects_token_without_returning_raw_value(tmp_path) -> None:
    secret = "gsk" + "_" + ("A" * 32)
    path = tmp_path / "leak.txt"
    path.write_text(f"token={secret}\n", encoding="utf-8")

    findings = scan_text(path, path.read_text(encoding="utf-8"), tmp_path)
    encoded = json.dumps([finding.__dict__ for finding in findings])

    assert len(findings) == 1
    assert findings[0].rule == "groq-key"
    assert secret not in encoded
    assert len(findings[0].fingerprint) == 12


def test_scanner_detects_non_placeholder_sensitive_assignment(tmp_path) -> None:
    assignment = "ADMIN_API_" + "TOKEN=real-production-value-1234567890\n"
    path = tmp_path / "settings.env"
    path.write_text(assignment, encoding="utf-8")

    findings = scan_text(path, path.read_text(encoding="utf-8"), tmp_path)

    assert any(item.rule == "sensitive-assignment" for item in findings)
    assert all("real-production-value" not in item.message for item in findings)


def test_scanner_allows_secret_references_and_empty_examples(tmp_path) -> None:
    path = tmp_path / "workflow.yml"
    path.write_text(
        "ADMIN_API_TOKEN: ${{ secrets.ADMIN_API_TOKEN }}\n"
        "GROQ_API_KEY=\n"
        "REMINDER_ENCRYPTION_KEY=example-placeholder\n",
        encoding="utf-8",
    )

    assert scan_text(path, path.read_text(encoding="utf-8"), tmp_path) == []


def test_scanner_allows_local_database_fixture_but_rejects_remote_password(tmp_path) -> None:
    path = tmp_path / "database.txt"
    local = "postgresql://postgres:postgres@127.0.0.1:5432/test"
    remote = "postgresql://service:" + "super-secret-password@db.example.invalid/app"
    path.write_text(f"{local}\n{remote}\n", encoding="utf-8")

    findings = scan_text(path, path.read_text(encoding="utf-8"), tmp_path)

    assert len(findings) == 1
    assert findings[0].rule == "database-credential"
    assert remote not in json.dumps(findings[0].__dict__)


def test_repository_scan_skips_binary_files(tmp_path) -> None:
    binary = tmp_path / "blob.bin"
    binary.write_bytes(b"\x00gsk" + b"_" + (b"A" * 40))

    assert scan_repository(tmp_path, files=[binary]) == []
