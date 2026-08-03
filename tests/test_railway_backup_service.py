"""Safety contract for the dedicated Railway PostgreSQL backup service."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_backup_service_is_daily_non_web_cron() -> None:
    config = json.loads(Path("railway.backup.json").read_text(encoding="utf-8"))
    build = config["build"]
    deploy = config["deploy"]

    assert build["dockerfilePath"] == "Dockerfile.backup"
    assert deploy["startCommand"] == "python scripts/postgres_backup.py"
    assert deploy["cronSchedule"] == "17 2 * * *"
    assert deploy["restartPolicyType"] == "NEVER"
    assert "healthcheckPath" not in deploy


def test_backup_image_prepares_root_owned_volume_then_drops_privileges() -> None:
    dockerfile = Path("Dockerfile.backup").read_text(encoding="utf-8")
    entrypoint = Path("scripts/backup_entrypoint.sh").read_text(encoding="utf-8")

    assert "FROM python:3.13-slim" in dockerfile
    assert "postgresql-client" in dockerfile
    assert "gosu" in dockerfile
    assert 'USER root' in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/backup-entrypoint"]' in dockerfile
    assert 'CMD ["python", "scripts/postgres_backup.py"]' in dockerfile
    assert 'RAILWAY_VOLUME_MOUNT_PATH' in entrypoint
    assert 'output directory must remain inside the mounted volume' in entrypoint
    assert 'chmod 0700 "$mount_path" "$output_dir"' in entrypoint
    assert 'exec gosu amthero "$@"' in entrypoint
    assert "BACKUP_ENCRYPTION_KEY=" not in dockerfile
    assert "DATABASE_URL=" not in dockerfile


def test_backup_entrypoint_has_valid_shell_syntax() -> None:
    subprocess.run(
        ["sh", "-n", "scripts/backup_entrypoint.sh"],
        check=True,
        capture_output=True,
        text=True,
    )
