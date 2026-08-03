"""Restore an AmtHero24 PostgreSQL backup with explicit destructive safeguards."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cryptography.fernet import Fernet, InvalidToken

from postgres_cli_env import postgres_cli_environment
from schema_recovery import require_current_manifest_schema, verify_restored_schema

_CONFIRMATION = "RESTORE_AMTHERO24"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _database_url(value: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned.startswith(("postgresql://", "postgres://")):
        raise ValueError("DATABASE_URL must be a PostgreSQL connection URL")
    return cleaned


def _database_identity(value: str) -> tuple[str, str, str, str]:
    """Return a password-free libpq identity for source/target isolation checks."""
    environment = postgres_cli_environment(_database_url(value), base_environment={})
    host = environment.get("PGHOST") or environment.get("PGHOSTADDR") or ""
    return (
        str(host).casefold(),
        str(environment.get("PGPORT") or "5432"),
        str(environment.get("PGUSER") or ""),
        str(environment.get("PGDATABASE") or ""),
    )


def _enabled(value: str) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _load_manifest(artifact: Path, manifest_path: Path | None) -> tuple[Path, dict[str, Any]]:
    path = manifest_path or artifact.with_name(artifact.name + ".manifest.json")
    if not path.exists():
        raise ValueError("Backup manifest is missing")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Backup manifest is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("Backup manifest must contain a JSON object")
    return path, payload


def _decrypt(artifact: Path, destination: Path, key: str) -> None:
    try:
        destination.write_bytes(Fernet(str(key or "").strip().encode("ascii")).decrypt(artifact.read_bytes()))
    except (InvalidToken, ValueError, TypeError) as exc:
        raise ValueError("Backup decryption failed") from exc


def restore_backup(
    database_url: str,
    artifact: Path,
    *,
    confirmation: str,
    restore_allowed: bool,
    encryption_key: str = "",
    manifest_path: Path | None = None,
    pg_restore_binary: str = "pg_restore",
    psql_binary: str = "psql",
    source_database_url: str = "",
) -> dict[str, Any]:
    """Verify, restore, and schema-certify one backup after explicit safeguards.

    Connection values are provided only through parsed libpq child variables. They are
    never placed in the process argument list or output. When a source URL is supplied,
    the target must have a distinct password-free libpq identity.
    """
    if not restore_allowed:
        raise PermissionError("RESTORE_ALLOWED=true is required")
    if str(confirmation or "").strip() != _CONFIRMATION:
        raise PermissionError(f"confirmation must equal {_CONFIRMATION}")

    url = _database_url(database_url)
    source_url = str(source_database_url or "").strip()
    if source_url and _database_identity(source_url) == _database_identity(url):
        raise PermissionError("restore target must be isolated from source database")

    artifact = Path(artifact)
    if not artifact.is_file():
        raise ValueError("Backup artifact does not exist")
    restore_binary = shutil.which(pg_restore_binary)
    sql_binary = shutil.which(psql_binary)
    if not restore_binary:
        raise RuntimeError(f"{pg_restore_binary} is not installed")
    if not sql_binary:
        raise RuntimeError(f"{psql_binary} is not installed")

    manifest_file, manifest = _load_manifest(artifact, manifest_path)
    if str(manifest.get("artifact") or "") != artifact.name:
        raise ValueError("Manifest artifact name does not match")
    if str(manifest.get("artifact_sha256") or "") != _sha256(artifact):
        raise ValueError("Backup artifact checksum mismatch")
    backup_identity = require_current_manifest_schema(manifest)

    with tempfile.TemporaryDirectory(prefix="amthero24-restore-") as temp_dir:
        plain = Path(temp_dir) / "database.dump"
        sql_file = Path(temp_dir) / "restore.sql"
        if bool(manifest.get("encrypted")):
            if not str(encryption_key or "").strip():
                raise ValueError("BACKUP_ENCRYPTION_KEY is required for this artifact")
            _decrypt(artifact, plain, encryption_key)
        else:
            shutil.copyfile(artifact, plain)

        expected_plaintext = str(manifest.get("plaintext_sha256") or "")
        if not expected_plaintext or expected_plaintext != _sha256(plain):
            raise ValueError("Decrypted backup checksum mismatch")

        subprocess.run(
            [
                restore_binary,
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
                "--exit-on-error",
                "--file",
                str(sql_file),
                str(plain),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=1800,
        )
        if not sql_file.exists() or sql_file.stat().st_size <= 0:
            raise RuntimeError("pg_restore did not produce SQL output")

        child_env = postgres_cli_environment(url, base_environment=os.environ)
        subprocess.run(
            [sql_binary, "--set", "ON_ERROR_STOP=on", "--file", str(sql_file)],
            check=True,
            env=child_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=1800,
        )

    restored_identity = verify_restored_schema(url, manifest)
    if restored_identity != backup_identity:
        raise RuntimeError("restored_schema_identity_mismatch")
    return {
        "status": "verified",
        "artifact": artifact.name,
        "manifest": manifest_file.name,
        "format": str(manifest.get("format") or "unknown"),
        "schema_version": restored_identity.version,
        "schema_contract": restored_identity.contract,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Restore and verify an AmtHero24 PostgreSQL backup.")
    parser.add_argument("artifact")
    parser.add_argument("--manifest")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--encryption-key", default=os.getenv("BACKUP_ENCRYPTION_KEY", ""))
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--pg-restore", default=os.getenv("PG_RESTORE_BINARY", "pg_restore"))
    parser.add_argument("--psql", default=os.getenv("PSQL_BINARY", "psql"))
    args = parser.parse_args(argv)

    try:
        result = restore_backup(
            args.database_url,
            Path(args.artifact),
            confirmation=args.confirm,
            restore_allowed=_enabled(os.getenv("RESTORE_ALLOWED", "false")),
            encryption_key=args.encryption_key,
            manifest_path=Path(args.manifest) if args.manifest else None,
            pg_restore_binary=args.pg_restore,
            psql_binary=args.psql,
            source_database_url=os.getenv("RESTORE_SOURCE_DATABASE_URL", ""),
        )
    except (PermissionError, ValueError, RuntimeError, subprocess.SubprocessError, OSError) as exc:
        print(f"Restore failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
