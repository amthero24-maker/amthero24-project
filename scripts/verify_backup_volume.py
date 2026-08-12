"""Fail closed when a production backup target is not a real mounted volume.

The preflight reads only local filesystem metadata. Its CLI emits bounded reason codes
and never prints mount paths, environment values, backup names, database URLs, secrets,
or file contents.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

_EXPECTED_MOUNT_PATH = "/backups"
_DEFAULT_MOUNTINFO_PATH = Path("/proc/self/mountinfo")
_MOUNT_ESCAPE = re.compile(r"\\([0-7]{3})")


class BackupVolumeError(RuntimeError):
    """A sanitized fail-closed backup-volume preflight failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class BackupVolumeStatus:
    status: str
    mount: str
    output: str


def _decode_mount_field(value: str) -> str:
    return _MOUNT_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), value)


def mounted_paths(mountinfo_text: str) -> set[str]:
    """Return normalized mount points from Linux mountinfo without other metadata."""
    paths: set[str] = set()
    for raw_line in str(mountinfo_text or "").splitlines():
        fields = raw_line.split()
        if len(fields) < 6:
            continue
        mount_point = _decode_mount_field(fields[4])
        if os.path.isabs(mount_point):
            paths.add(os.path.normpath(mount_point))
    return paths


def _inside_mount(mount_path: str, output_dir: str) -> bool:
    try:
        return os.path.commonpath((mount_path, output_dir)) == mount_path
    except ValueError:
        return False


def verify_backup_volume(
    mount_path: str,
    output_dir: str,
    *,
    mountinfo_text: str,
    expected_mount_path: str = _EXPECTED_MOUNT_PATH,
) -> BackupVolumeStatus:
    """Require an exact mounted directory and an output path contained within it."""
    raw_mount = str(mount_path or "").strip()
    if not raw_mount:
        raise BackupVolumeError("missing_mount_variable")
    if not os.path.isabs(raw_mount):
        raise BackupVolumeError("mount_not_absolute")

    normalized_mount = os.path.normpath(raw_mount)
    normalized_expected = os.path.normpath(str(expected_mount_path or "").strip())
    if normalized_mount == "/":
        raise BackupVolumeError("root_mount_forbidden")
    if not normalized_expected or normalized_mount != normalized_expected:
        raise BackupVolumeError("unexpected_mount_path")
    if not os.path.isdir(normalized_mount):
        raise BackupVolumeError("mount_directory_missing")

    raw_output = str(output_dir or "").strip()
    if not raw_output:
        raise BackupVolumeError("missing_output_directory")
    if not os.path.isabs(raw_output):
        raise BackupVolumeError("output_not_absolute")

    real_mount = os.path.realpath(normalized_mount)
    real_output = os.path.realpath(os.path.normpath(raw_output))
    if not _inside_mount(real_mount, real_output):
        raise BackupVolumeError("output_outside_mount")

    attached = {
        os.path.realpath(path)
        for path in mounted_paths(mountinfo_text)
        if os.path.exists(path)
    }
    if real_mount not in attached:
        raise BackupVolumeError("mount_not_attached")

    return BackupVolumeStatus(
        status="ready",
        mount="attached",
        output="inside_mount",
    )


def verify_backup_volume_from_system(
    mount_path: str,
    output_dir: str,
    *,
    mountinfo_path: Path = _DEFAULT_MOUNTINFO_PATH,
    expected_mount_path: str = _EXPECTED_MOUNT_PATH,
) -> BackupVolumeStatus:
    try:
        mountinfo_text = Path(mountinfo_path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise BackupVolumeError("mountinfo_unavailable") from exc
    return verify_backup_volume(
        mount_path,
        output_dir,
        mountinfo_text=mountinfo_text,
        expected_mount_path=expected_mount_path,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify that AmtHero24 backup output is on a real mounted volume."
    )
    parser.add_argument(
        "--mount-path",
        default=os.getenv("RAILWAY_VOLUME_MOUNT_PATH", ""),
    )
    parser.add_argument(
        "--output-dir",
        default=os.getenv("BACKUP_OUTPUT_DIR", _EXPECTED_MOUNT_PATH),
    )
    parser.add_argument(
        "--mountinfo",
        type=Path,
        default=_DEFAULT_MOUNTINFO_PATH,
    )
    args = parser.parse_args(argv)

    try:
        status = verify_backup_volume_from_system(
            str(args.mount_path or ""),
            str(args.output_dir or ""),
            mountinfo_path=args.mountinfo,
        )
    except BackupVolumeError as exc:
        print(
            json.dumps(
                {"status": "failed", "reason": exc.code},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1

    print(json.dumps(asdict(status), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
