"""Safety contract for the dedicated Railway PostgreSQL backup service."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


_BACKUP_MODULE_COMMAND = "python -m scripts.postgres_backup"


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


def test_backup_image_prepares_root_owned_volume_then_drops_privileges() -> None:
    dockerfile = Path("Dockerfile.backup").read_text(encoding="utf-8")
    entrypoint = Path("scripts/backup_entrypoint.sh").read_text(encoding="utf-8")

    assert "FROM python:3.13-slim" in dockerfile
    assert "postgresql-client" in dockerfile
    assert "gosu" in dockerfile
    assert 'USER root' in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/backup-entrypoint"]' in dockerfile
    assert 'CMD ["python", "-m", "scripts.postgres_backup"]' in dockerfile
    assert 'RAILWAY_VOLUME_MOUNT_PATH' in entrypoint
    assert 'output directory must remain inside the mounted volume' in entrypoint
    assert 'chmod 0700 "$mount_path" "$output_dir"' in entrypoint
    assert 'exec gosu amthero "$@"' in entrypoint
    assert "BACKUP_ENCRYPTION_KEY=" not in dockerfile
    assert "DATABASE_URL=" not in dockerfile


def test_backup_module_entrypoint_loads_repository_dependencies() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.postgres_backup", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Create encrypted" in completed.stdout


def test_backup_entrypoint_has_valid_shell_syntax() -> None:
    subprocess.run(
        ["sh", "-n", "scripts/backup_entrypoint.sh"],
        check=True,
        capture_output=True,
        text=True,
    )
