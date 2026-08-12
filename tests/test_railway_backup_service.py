"""Safety contract for the dedicated Railway PostgreSQL backup service."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.postgres_backup import main as backup_main
from scripts.verify_backup_volume import (
    BackupVolumeError,
    BackupVolumeStatus,
    main as volume_main,
    mounted_paths,
    verify_backup_volume,
)


_BACKUP_MODULE_COMMAND = "python -m scripts.postgres_backup"


def _mountinfo_line(path: Path) -> str:
    escaped = str(path).replace("\\", "\\134").replace(" ", "\\040")
    return f"36 25 0:32 / {escaped} rw,nosuid,nodev - ext4 /dev/safe rw\n"


def test_backup_service_is_daily_non_web_cron() -> None:
    config = json.loads(Path("railway.backup.json").read_text(encoding="utf-8"))
    build = config["build"]
    deploy = config["deploy"]

    assert build["dockerfilePath"] == "Dockerfile.backup"
    assert deploy["startCommand"] == _BACKUP_MODULE_COMMAND
    assert deploy["cronSchedule"] == "17 2 * * *"
    assert deploy["restartPolicyType"] == "NEVER"
    assert "healthcheckPath" not in deploy


def test_backup_certification_profile_is_explicit_one_shot() -> None:
    config = json.loads(
        Path("railway.backup.certification.json").read_text(encoding="utf-8")
    )
    build = config["build"]
    deploy = config["deploy"]

    assert build["dockerfilePath"] == "Dockerfile.backup"
    assert deploy["startCommand"] == _BACKUP_MODULE_COMMAND
    assert deploy["restartPolicyType"] == "NEVER"
    assert "cronSchedule" not in deploy
    assert "healthcheckPath" not in deploy


def test_backup_image_requires_real_volume_then_drops_privileges() -> None:
    dockerfile = Path("Dockerfile.backup").read_text(encoding="utf-8")
    entrypoint = Path("scripts/backup_entrypoint.sh").read_text(encoding="utf-8")

    assert "FROM python:3.13-slim" in dockerfile
    assert "postgresql-client" in dockerfile
    assert "gosu" in dockerfile
    assert "USER root" in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/backup-entrypoint"]' in dockerfile
    assert 'CMD ["python", "-m", "scripts.postgres_backup"]' in dockerfile
    assert 'mount_path="${RAILWAY_VOLUME_MOUNT_PATH:-}"' in entrypoint
    assert 'RAILWAY_VOLUME_MOUNT_PATH:-/backups' not in entrypoint
    assert "python -m scripts.verify_backup_volume" in entrypoint
    assert 'output_outside_mount' in entrypoint
    assert 'chmod 0700 "$mount_path" "$output_dir"' in entrypoint
    assert 'exec gosu amthero "$@"' in entrypoint
    assert "BACKUP_ENCRYPTION_KEY=" not in dockerfile
    assert "DATABASE_URL=" not in dockerfile


def test_mountinfo_parser_decodes_linux_escaped_mount_points(tmp_path: Path) -> None:
    mount = tmp_path / "volume root"
    mount.mkdir()

    assert mounted_paths(_mountinfo_line(mount)) == {str(mount)}


def test_volume_preflight_accepts_exact_mount_and_nested_output(tmp_path: Path) -> None:
    mount = tmp_path / "volume root"
    mount.mkdir()
    output = mount / "daily"

    status = verify_backup_volume(
        str(mount),
        str(output),
        mountinfo_text=_mountinfo_line(mount),
        expected_mount_path=str(mount),
    )

    assert status == BackupVolumeStatus(
        status="ready",
        mount="attached",
        output="inside_mount",
    )


def test_volume_preflight_rejects_missing_mount_variable() -> None:
    with pytest.raises(BackupVolumeError) as raised:
        verify_backup_volume(
            "",
            "/backups",
            mountinfo_text="",
        )

    assert raised.value.code == "missing_mount_variable"


def test_volume_preflight_rejects_image_directory_without_mount(tmp_path: Path) -> None:
    mount = tmp_path / "backups"
    mount.mkdir()

    with pytest.raises(BackupVolumeError) as raised:
        verify_backup_volume(
            str(mount),
            str(mount),
            mountinfo_text="",
            expected_mount_path=str(mount),
        )

    assert raised.value.code == "mount_not_attached"


def test_volume_preflight_rejects_output_symlink_escape(tmp_path: Path) -> None:
    mount = tmp_path / "backups"
    outside = tmp_path / "outside"
    mount.mkdir()
    outside.mkdir()
    (mount / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(BackupVolumeError) as raised:
        verify_backup_volume(
            str(mount),
            str(mount / "escape" / "daily"),
            mountinfo_text=_mountinfo_line(mount),
            expected_mount_path=str(mount),
        )

    assert raised.value.code == "output_outside_mount"


def test_volume_cli_reports_only_bounded_reason_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text("", encoding="utf-8")
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    monkeypatch.setenv("BACKUP_OUTPUT_DIR", "/backups")

    exit_code = volume_main(["--mountinfo", str(mountinfo)])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert json.loads(captured.err) == {
        "reason": "missing_mount_variable",
        "status": "failed",
    }
    assert "/backups" not in captured.err


def test_backup_cli_checks_volume_before_database_or_dump(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with patch(
        "scripts.postgres_backup.verify_backup_volume_from_system",
        side_effect=BackupVolumeError("mount_not_attached"),
    ), patch("scripts.postgres_backup.create_backup") as create:
        exit_code = backup_main(
            [
                "--database-url",
                "postgresql://private.invalid/database",
                "--encryption-key",
                "not-inspected-before-volume-check",
            ]
        )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "BackupVolumeError: mount_not_attached" in captured.err
    assert "private.invalid" not in captured.err
    assert "not-inspected-before-volume-check" not in captured.err
    create.assert_not_called()


def test_backup_module_entrypoint_loads_repository_dependencies() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.postgres_backup", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Create an encrypted" in completed.stdout


def test_backup_direct_script_entrypoint_loads_repository_dependencies() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/postgres_backup.py", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Create an encrypted" in completed.stdout


def test_backup_volume_module_entrypoint_loads_repository_dependencies() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.verify_backup_volume", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "real mounted volume" in completed.stdout


def test_backup_entrypoint_has_valid_shell_syntax() -> None:
    subprocess.run(
        ["sh", "-n", "scripts/backup_entrypoint.sh"],
        check=True,
        capture_output=True,
        text=True,
    )
