"""Fail-closed, privacy-safe certification of the PostgreSQL recovery pipeline.

The production restore drill certifies a concrete backup/restore/schema toolchain rather
than one artifact timestamp forever. Normal daily backups may remain launch-ready only
while this exact pipeline is unchanged and the owner-approved isolated restore evidence
is still inside its bounded validity window.

Only repository file identities and bounded status codes are handled here. Artifact
paths, hashes, database URLs, credentials, phone numbers, messages, documents, and
backup contents are never returned or logged.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

CERTIFIED_SOURCE_COMMIT = "54286df776c81808f5fcdde471bb316b966d2a74"
_DEFAULT_CERTIFICATION_MAX_AGE_HOURS = 168
_MIN_CERTIFICATION_MAX_AGE_HOURS = 24
_MAX_CERTIFICATION_MAX_AGE_HOURS = 720

# Git blob identities from the exact production pipeline used by the successful
# owner-authorized isolated restore on CERTIFIED_SOURCE_COMMIT. The list intentionally
# covers the image/toolchain, schema contract, backup/restore implementation, persistent
# volume guard, and every Railway backup/restore profile. Updating any entry requires a
# new real isolated restore before the certified identities may be changed.
_CERTIFIED_BLOBS: dict[str, str] = {
    "Dockerfile.backup": "4dce2526406d3522cd879d8aae19cf61680d7545",
    "backup_recovery.py": "8e62350c0dcb95115d26bd35dcc34b069b435d1a",
    "database_migrations.py": "af798d645e9aa8b7e5f18e68b6162bd07439b71d",
    "postgres_cli_env.py": "145c1efdca26462ab90ac2aa8f7a229803d96914",
    "requirements.txt": "06ea58b09af2925683f84444142fcd5ec7b32bc2",
    "schema_recovery.py": "c7ce6a01644f44a162018b4e3a1ce3ea7a6aea1e",
    "railway.backup.certification.json": "57366eef4efa2e68097a748bad7d2850b991d0ac",
    "railway.backup.json": "38a0e564a2c4608c4273cbac60109350624c5f4f",
    "railway.backup.retention-probe.json": "1cae31f9785ce68e732aaa84393ebd1c4e3cc55e",
    "railway.restore.certification.json": "655676c9e0b878ef724d73921f3199df5baaa8b6",
    "scripts/backup_entrypoint.sh": "2fafdb28f15be899abf85d7581a4bf30837ae7d6",
    "scripts/backup_retention_probe.py": "adc32651435758aedcb139ee7a05c5b8b8d3c00e",
    "scripts/postgres_backup.py": "0d639200fd2360c63c772649ac7f8fd4ec115d1e",
    "scripts/postgres_restore.py": "b3d500ce593b6ee61251ac8a20971dc13d937d0f",
    "scripts/restore_certification_entrypoint.sh": "ee37d4bf4b482497bd41b0d4492d07d8c6e3321a",
    "scripts/verify_backup_volume.py": "2cc0334b46a81f48780c001f1956a05216918c72",
}


@dataclass(frozen=True)
class RecoveryPipelineAssessment:
    status: str
    code: str
    checked_files: int

    @property
    def ready(self) -> bool:
        return self.status == "ready"


def _git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    payload = f"blob {len(data)}\0".encode("ascii") + data
    return hashlib.sha1(payload, usedforsecurity=False).hexdigest()


def assess_recovery_pipeline(
    root: Path | None = None,
) -> RecoveryPipelineAssessment:
    """Verify the current checkout against the actually restore-certified pipeline."""
    try:
        base = (root or Path(__file__).resolve().parent).resolve(strict=True)
    except (OSError, RuntimeError):
        return RecoveryPipelineAssessment("blocked", "pipeline_root_invalid", 0)

    checked = 0
    for relative_path, expected_blob in sorted(_CERTIFIED_BLOBS.items()):
        candidate = base / relative_path
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(base)
        except FileNotFoundError:
            return RecoveryPipelineAssessment(
                "blocked", "pipeline_file_missing", checked
            )
        except (OSError, RuntimeError, ValueError):
            return RecoveryPipelineAssessment(
                "blocked", "pipeline_file_invalid", checked
            )
        if not resolved.is_file():
            return RecoveryPipelineAssessment(
                "blocked", "pipeline_file_invalid", checked
            )
        try:
            actual_blob = _git_blob_sha(resolved)
        except OSError:
            return RecoveryPipelineAssessment(
                "blocked", "pipeline_file_invalid", checked
            )
        if not hmac.compare_digest(actual_blob, expected_blob):
            return RecoveryPipelineAssessment("blocked", "pipeline_drift", checked)
        checked += 1

    return RecoveryPipelineAssessment("ready", "pipeline_certified", checked)


def restore_certification_max_age_hours(
    environment: Mapping[str, str] | None = None,
) -> int | None:
    """Return a bounded certification window, or None for explicit invalid input."""
    if environment is None:
        raw = os.getenv(
            "PRODUCTION_BACKUP_RESTORE_CERTIFICATION_MAX_AGE_HOURS",
            str(_DEFAULT_CERTIFICATION_MAX_AGE_HOURS),
        )
    else:
        raw = environment.get(
            "PRODUCTION_BACKUP_RESTORE_CERTIFICATION_MAX_AGE_HOURS",
            str(_DEFAULT_CERTIFICATION_MAX_AGE_HOURS),
        )
    try:
        parsed = int(str(raw or "").strip())
    except (TypeError, ValueError):
        return None
    if not (
        _MIN_CERTIFICATION_MAX_AGE_HOURS
        <= parsed
        <= _MAX_CERTIFICATION_MAX_AGE_HOURS
    ):
        return None
    return parsed
