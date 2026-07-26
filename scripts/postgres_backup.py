"""Create encrypted, integrity-checked PostgreSQL backups for AmtHero24.

Designed for a Railway cron/service with the PostgreSQL private DATABASE_URL and a
persistent backup volume. The database URL is passed to pg_dump through the child
environment and is never printed or placed in command-line arguments.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


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


def _fernet(key: str) -> Fernet:
    cleaned = str(key or "").strip()
    if not cleaned:
        raise ValueError("BACKUP_ENCRYPTION_KEY is required unless --allow-unencrypted is used")
    try:
        return Fernet(cleaned.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise ValueError("BACKUP_ENCRYPTION_KEY must be a valid Fernet key") from exc


def _rotate(output_dir: Path, keep: int) -> int:
    artifacts = sorted(output_dir.glob("amthero24-*.dump*"), key=lambda path: path.stat().st_mtime, reverse=True)
    removed = 0
    for artifact in artifacts[max(1, int(keep)):]:
        if artifact.suffix == ".json":
            continue
        manifest = artifact.with_name(artifact.name + ".manifest.json")
        artifact.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        removed += 1
    return removed


def create_backup(
    database_url: str,
    output_dir: Path,
    *,
    encryption_key: str = "",
    allow_unencrypted: bool = False,
    keep: int = 14,
    pg_dump_binary: str = "pg_dump",
    now: datetime | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    """Create one atomic custom-format backup and a non-secret manifest."""
    url = _database_url(database_url)
    binary = shutil.which(pg_dump_binary)
    if not binary:
        raise RuntimeError(f"{pg_dump_binary} is not installed")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    current = (now or datetime.now(UTC)).astimezone(UTC)
    stamp = current.strftime("%Y%m%dT%H%M%SZ")
    encrypted = bool(str(encryption_key or "").strip())
    if not encrypted and not allow_unencrypted:
        raise ValueError("Refusing an unencrypted backup; provide BACKUP_ENCRYPTION_KEY or --allow-unencrypted")

    with tempfile.TemporaryDirectory(prefix="amthero24-backup-") as temp_dir:
        plain = Path(temp_dir) / "database.dump"
        child_env = os.environ.copy()
        child_env["PGDATABASE"] = url
        child_env.pop("DATABASE_URL", None)
        subprocess.run(
            [binary, "--format=custom", "--no-owner", "--no-privileges", "--file", str(plain)],
            check=True,
            env=child_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=1800,
        )
        if not plain.exists() or plain.stat().st_size <= 0:
            raise RuntimeError("pg_dump completed without producing a backup")

        plaintext_sha = _sha256(plain)
        final_name = f"amthero24-{stamp}.dump.fernet" if encrypted else f"amthero24-{stamp}.dump"
        final_path = output_dir / final_name
        temporary_final = output_dir / f".{final_name}.tmp"

        if encrypted:
            cipher = _fernet(encryption_key)
            temporary_final.write_bytes(cipher.encrypt(plain.read_bytes()))
        else:
            shutil.copyfile(plain, temporary_final)
        os.replace(temporary_final, final_path)

    manifest = {
        "format": "pg_dump_custom",
        "created_at": current.isoformat(),
        "encrypted": encrypted,
        "artifact": final_path.name,
        "artifact_size_bytes": final_path.stat().st_size,
        "artifact_sha256": _sha256(final_path),
        "plaintext_sha256": plaintext_sha,
        "restore_confirmation": "RESTORE_AMTHERO24",
    }
    manifest_path = final_path.with_name(final_path.name + ".manifest.json")
    temporary_manifest = manifest_path.with_name("." + manifest_path.name + ".tmp")
    temporary_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary_manifest, manifest_path)
    _rotate(output_dir, keep)
    return final_path, manifest_path, manifest


def decrypt_backup(artifact: Path, destination: Path, *, encryption_key: str) -> None:
    """Decrypt an artifact for verification/tests without restoring it."""
    try:
        destination.write_bytes(_fernet(encryption_key).decrypt(Path(artifact).read_bytes()))
    except InvalidToken as exc:
        raise ValueError("Backup decryption failed") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create an encrypted AmtHero24 PostgreSQL backup.")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""))
    parser.add_argument("--output-dir", default=os.getenv("BACKUP_OUTPUT_DIR", "/backups"))
    parser.add_argument("--encryption-key", default=os.getenv("BACKUP_ENCRYPTION_KEY", ""))
    parser.add_argument("--allow-unencrypted", action="store_true")
    parser.add_argument("--keep", type=int, default=int(os.getenv("BACKUP_KEEP_COUNT", "14")))
    parser.add_argument("--pg-dump", default=os.getenv("PG_DUMP_BINARY", "pg_dump"))
    args = parser.parse_args(argv)

    try:
        artifact, manifest, metadata = create_backup(
            args.database_url,
            Path(args.output_dir),
            encryption_key=args.encryption_key,
            allow_unencrypted=args.allow_unencrypted,
            keep=max(1, args.keep),
            pg_dump_binary=args.pg_dump,
        )
    except (ValueError, RuntimeError, subprocess.SubprocessError, OSError) as exc:
        print(f"Backup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"status": "created", "artifact": str(artifact), "manifest": str(manifest), "size_bytes": metadata["artifact_size_bytes"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
