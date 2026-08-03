"""Safety contract for the dedicated Railway PostgreSQL backup service."""
from __future__ import annotations

import json
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


def test_backup_image_contains_postgres_client_and_runs_unprivileged() -> None:
    dockerfile = Path("Dockerfile.backup").read_text(encoding="utf-8")

    assert "FROM python:3.13-slim" in dockerfile
    assert "postgresql-client" in dockerfile
    assert "USER amthero" in dockerfile
    assert 'CMD ["python", "scripts/postgres_backup.py"]' in dockerfile
    assert "BACKUP_ENCRYPTION_KEY=" not in dockerfile
    assert "DATABASE_URL=" not in dockerfile
