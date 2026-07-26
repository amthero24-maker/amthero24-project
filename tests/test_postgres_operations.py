"""Tests for PostgreSQL backup and restore safety boundaries."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

from schema_recovery import SchemaRecoveryError, expected_schema_identity
from scripts.postgres_backup import create_backup
from scripts.postgres_restore import restore_backup


DATABASE_URL = "postgresql://db.internal/amthero24_test"
SCHEMA_IDENTITY = expected_schema_identity()


def test_backup_is_encrypted_schema_bound_and_database_url_is_not_in_argv(tmp_path) -> None:
    key = Fernet.generate_key().decode("ascii")
    commands: list[list[str]] = []

    def fake_run(command, **kwargs):
        commands.append(list(command))
        output = Path(command[command.index("--file") + 1])
        output.write_bytes(b"custom-postgres-dump")

    with patch("scripts.postgres_backup.shutil.which", return_value="/usr/bin/pg_dump"), patch(
        "scripts.postgres_backup.inspect_database_schema", return_value=SCHEMA_IDENTITY
    ), patch("scripts.postgres_backup.subprocess.run", side_effect=fake_run):
        artifact, manifest_path, manifest = create_backup(
            DATABASE_URL,
            tmp_path,
            encryption_key=key,
            now=datetime(2026, 7, 26, 12, tzinfo=UTC),
        )

    assert artifact.name.endswith(".dump.fernet")
    assert artifact.read_bytes() != b"custom-postgres-dump"
    assert Fernet(key.encode("ascii")).decrypt(artifact.read_bytes()) == b"custom-postgres-dump"
    assert manifest_path.exists()
    assert manifest["encrypted"] is True
    assert manifest["schema_version"] == SCHEMA_IDENTITY.version
    assert manifest["schema_checksum"] == SCHEMA_IDENTITY.checksum
    assert manifest["schema_ledger_entries"] == SCHEMA_IDENTITY.ledger_entries
    assert manifest["schema_contract"] == "valid"
    assert DATABASE_URL not in " ".join(commands[0])


def test_backup_refuses_plaintext_before_schema_inspection(tmp_path) -> None:
    with patch("scripts.postgres_backup.inspect_database_schema") as inspect, patch(
        "scripts.postgres_backup.shutil.which", return_value="/usr/bin/pg_dump"
    ):
        with pytest.raises(ValueError, match="Refusing an unencrypted backup"):
            create_backup(DATABASE_URL, tmp_path)
    inspect.assert_not_called()


def test_backup_schema_failure_prevents_pg_dump(tmp_path) -> None:
    key = Fernet.generate_key().decode("ascii")
    with patch("scripts.postgres_backup.shutil.which", return_value="/usr/bin/pg_dump"), patch(
        "scripts.postgres_backup.inspect_database_schema",
        side_effect=SchemaRecoveryError("database_schema_contract_invalid"),
    ), patch("scripts.postgres_backup.subprocess.run") as run:
        with pytest.raises(SchemaRecoveryError) as raised:
            create_backup(DATABASE_URL, tmp_path, encryption_key=key)
    assert raised.value.code == "database_schema_contract_invalid"
    run.assert_not_called()


def _encrypted_fixture(tmp_path: Path) -> tuple[Path, str]:
    key = Fernet.generate_key().decode("ascii")
    plaintext = b"verified-custom-dump"
    artifact = tmp_path / "amthero24-test.dump.fernet"
    artifact.write_bytes(Fernet(key.encode("ascii")).encrypt(plaintext))
    manifest = {
        "format": "pg_dump_custom",
        "encrypted": True,
        "artifact": artifact.name,
        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "plaintext_sha256": hashlib.sha256(plaintext).hexdigest(),
        **SCHEMA_IDENTITY.manifest_fields(),
    }
    artifact.with_name(artifact.name + ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return artifact, key


def test_restore_requires_two_independent_confirmations(tmp_path) -> None:
    artifact, key = _encrypted_fixture(tmp_path)
    with pytest.raises(PermissionError, match="RESTORE_ALLOWED"):
        restore_backup(
            DATABASE_URL,
            artifact,
            confirmation="RESTORE_AMTHERO24",
            restore_allowed=False,
            encryption_key=key,
        )
    with pytest.raises(PermissionError, match="confirmation"):
        restore_backup(
            DATABASE_URL,
            artifact,
            confirmation="wrong",
            restore_allowed=True,
            encryption_key=key,
        )


def test_incompatible_manifest_blocks_restore_before_subprocess(tmp_path) -> None:
    artifact, key = _encrypted_fixture(tmp_path)
    manifest_path = artifact.with_name(artifact.name + ".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = 999
    manifest["schema_ledger_entries"] = 999
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with patch("scripts.postgres_restore.shutil.which", return_value="/usr/bin/tool"), patch(
        "scripts.postgres_restore.subprocess.run"
    ) as run:
        with pytest.raises(SchemaRecoveryError) as raised:
            restore_backup(
                DATABASE_URL,
                artifact,
                confirmation="RESTORE_AMTHERO24",
                restore_allowed=True,
                encryption_key=key,
            )
    assert raised.value.code == "backup_schema_version_incompatible"
    run.assert_not_called()


def test_restore_verifies_schema_then_uses_pgdatabase_without_url_argv(tmp_path) -> None:
    artifact, key = _encrypted_fixture(tmp_path)
    calls: list[tuple[list[str], dict]] = []

    def fake_run(command, **kwargs):
        calls.append((list(command), kwargs))
        if command[0] == "/usr/bin/pg_restore":
            sql_file = Path(command[command.index("--file") + 1])
            sql_file.write_text("SELECT 1;", encoding="utf-8")

    def fake_which(name: str):
        return f"/usr/bin/{name}"

    with patch("scripts.postgres_restore.shutil.which", side_effect=fake_which), patch(
        "scripts.postgres_restore.subprocess.run", side_effect=fake_run
    ), patch("scripts.postgres_restore.verify_restored_schema", return_value=SCHEMA_IDENTITY) as verify:
        result = restore_backup(
            DATABASE_URL,
            artifact,
            confirmation="RESTORE_AMTHERO24",
            restore_allowed=True,
            encryption_key=key,
        )

    assert result == {
        "status": "verified",
        "artifact": artifact.name,
        "manifest": artifact.name + ".manifest.json",
        "format": "pg_dump_custom",
        "schema_version": SCHEMA_IDENTITY.version,
        "schema_contract": "valid",
    }
    verify.assert_called_once()
    assert verify.call_args.args[0] == DATABASE_URL
    assert len(calls) == 2
    for command, _ in calls:
        assert DATABASE_URL not in " ".join(command)
    psql_command, psql_kwargs = calls[1]
    assert psql_command[0] == "/usr/bin/psql"
    assert psql_kwargs["env"]["PGDATABASE"] == DATABASE_URL
