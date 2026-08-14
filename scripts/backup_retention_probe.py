"""One-shot synthetic proof of backup pair retention on the Railway volume.

The probe creates only fixed synthetic artifact/manifest pairs inside a unique hidden
subdirectory of the verified `/backups` mount, exercises the production rotation
function against that subdirectory, verifies paired deletion, and removes the complete
synthetic directory before returning. It never reads, lists, hashes, modifies, or
removes real production backup artifacts.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.postgres_backup import _rotate
from scripts.verify_backup_volume import (
    BackupVolumeError,
    verify_backup_volume_from_system,
)

CONFIRMATION = "SYNTHETIC_RETENTION_PROBE"
_TOTAL_PAIRS = 5
_KEEP_PAIRS = 2
_PREFIX = ".amthero24-retention-proof-"


class RetentionProbeError(RuntimeError):
    """Bounded fail-closed retention-probe error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _write_synthetic_pair(directory: Path, index: int) -> None:
    stamp = f"2000010{index + 1}T000000Z"
    artifact = directory / f"amthero24-{stamp}.dump.fernet"
    manifest = artifact.with_name(artifact.name + ".manifest.json")
    artifact.write_bytes(b"synthetic-retention-proof\n")
    manifest.write_text('{"synthetic":true}\n', encoding="utf-8")
    timestamp = 1_700_000_000 + index
    os.utime(artifact, (timestamp, timestamp))
    os.utime(manifest, (timestamp, timestamp))


def run_retention_probe(mount_path: str) -> dict[str, Any]:
    """Verify production rotation behavior without touching real backup files."""
    mount = str(mount_path or "").strip()
    probe_dir = Path(mount) / f"{_PREFIX}{secrets.token_hex(8)}"
    verify_backup_volume_from_system(mount, str(probe_dir))

    result: dict[str, Any] | None = None
    try:
        probe_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
        for index in range(_TOTAL_PAIRS):
            _write_synthetic_pair(probe_dir, index)

        removed = _rotate(probe_dir, _KEEP_PAIRS)
        artifacts = sorted(
            path
            for path in probe_dir.glob("amthero24-*.dump.fernet")
            if path.is_file()
        )
        manifests = sorted(
            path
            for path in probe_dir.glob("amthero24-*.dump.fernet.manifest.json")
            if path.is_file()
        )
        expected_removed = _TOTAL_PAIRS - _KEEP_PAIRS
        if removed != expected_removed:
            raise RetentionProbeError("removed_count_mismatch")
        if len(artifacts) != _KEEP_PAIRS or len(manifests) != _KEEP_PAIRS:
            raise RetentionProbeError("retained_count_mismatch")
        if any(
            not artifact.with_name(artifact.name + ".manifest.json").is_file()
            for artifact in artifacts
        ):
            raise RetentionProbeError("pair_integrity_failed")

        result = {
            "status": "verified",
            "created_pairs": _TOTAL_PAIRS,
            "kept_pairs": _KEEP_PAIRS,
            "removed_pairs": expected_removed,
            "paired_deletion": True,
            "scope": "synthetic_subdirectory_only",
        }
    except RetentionProbeError:
        raise
    except (OSError, ValueError) as exc:
        raise RetentionProbeError("filesystem_operation_failed") from exc
    finally:
        try:
            shutil.rmtree(probe_dir)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise RetentionProbeError("cleanup_failed") from exc

    if probe_dir.exists():
        raise RetentionProbeError("cleanup_failed")
    if result is None:
        raise RetentionProbeError("result_missing")
    result["cleanup"] = "complete"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify backup retention in a disposable synthetic volume subdirectory."
    )
    parser.add_argument("--confirm", default="")
    parser.add_argument(
        "--mount-path",
        default=os.getenv("RAILWAY_VOLUME_MOUNT_PATH", ""),
    )
    args = parser.parse_args(argv)

    if str(args.confirm or "").strip() != CONFIRMATION:
        print(
            json.dumps(
                {"status": "failed", "reason": "confirmation_required"},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1

    try:
        report = run_retention_probe(str(args.mount_path or ""))
    except BackupVolumeError as exc:
        reason = exc.code
    except RetentionProbeError as exc:
        reason = exc.code
    except Exception:
        reason = "unexpected_error"
    else:
        print(json.dumps(report, sort_keys=True))
        return 0

    print(
        json.dumps({"status": "failed", "reason": reason}, sort_keys=True),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
