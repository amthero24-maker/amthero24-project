"""Tests for deterministic Railway configuration and recovery safety."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from railway_contract import report_payload, validate_railway_contract
from scripts.postgres_restore import restore_backup


ENTRYPOINT = (
    "uvicorn webhook_security:app --host 0.0.0.0 --port $PORT "
    "--log-config logging.railway.json"
)
PROCFILE = f"web: {ENTRYPOINT}\n"


def _valid_logging_config():
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(levelprefix)s %(name)s %(message)s",
                "use_colors": None,
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
                "use_colors": None,
            },
        },
        "filters": {
            "max_info": {
                "()": "railway_logging.MaxLevelFilter",
                "max_level": "INFO",
            }
        },
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "filters": ["max_info"],
                "stream": "ext://sys.stdout",
            },
            "stderr": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "level": "WARNING",
                "stream": "ext://sys.stderr",
            },
            "access_stdout": {
                "class": "logging.StreamHandler",
                "formatter": "access",
                "filters": ["max_info"],
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["stdout", "stderr"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn.error": {"level": "INFO"},
            "uvicorn.access": {
                "handlers": ["access_stdout", "stderr"],
                "level": "INFO",
                "propagate": False,
            },
        },
        "root": {"handlers": ["stdout", "stderr"], "level": "INFO"},
    }


def _write(
    root,
    config,
    procfile=PROCFILE,
    *,
    logging_config=None,
):
    (root / "railway.json").write_text(json.dumps(config), encoding="utf-8")
    (root / "Procfile").write_text(procfile, encoding="utf-8")
    (root / "logging.railway.json").write_text(
        json.dumps(logging_config or _valid_logging_config()),
        encoding="utf-8",
    )


def _valid_config():
    return {
        "$schema": "https://railway.com/railway.schema.json",
        "deploy": {
            "startCommand": ENTRYPOINT,
            "healthcheckPath": "/ready",
            "healthcheckTimeout": 300,
            "restartPolicyType": "ON_FAILURE",
            "restartPolicyMaxRetries": 10,
            "overlapSeconds": 30,
            "drainingSeconds": 15,
        },
    }


def test_repository_railway_contract_passes() -> None:
    findings = validate_railway_contract(".")
    assert findings
    assert all(item.passed for item in findings)
    assert report_payload(findings)["passed"] is True


def test_repository_start_commands_load_same_logging_config() -> None:
    config = json.loads(Path("railway.json").read_text(encoding="utf-8"))
    start_command = config["deploy"]["startCommand"]

    assert start_command == ENTRYPOINT
    assert Path("Procfile").read_text(encoding="utf-8") == f"web: {start_command}\n"
    assert Path("logging.railway.json").is_file()


def test_contract_rejects_liveness_path_and_unsafe_handoff(tmp_path) -> None:
    config = _valid_config()
    config["deploy"].update({
        "healthcheckPath": "/health",
        "healthcheckTimeout": 10,
        "restartPolicyType": "NEVER",
        "restartPolicyMaxRetries": 0,
        "overlapSeconds": 5,
        "drainingSeconds": 30,
    })
    _write(tmp_path, config)

    failed = {item.code for item in validate_railway_contract(tmp_path) if not item.passed}

    assert failed == {
        "healthcheck_path",
        "healthcheck_timeout",
        "restart_policy",
        "restart_retries",
        "graceful_handoff",
    }


def test_contract_rejects_wrong_entrypoint_and_missing_schema(tmp_path) -> None:
    config = _valid_config()
    config.pop("$schema")
    config["deploy"]["startCommand"] = "uvicorn app:app --port 9000"
    _write(tmp_path, config)

    failed = {item.code for item in validate_railway_contract(tmp_path) if not item.passed}

    assert failed == {"schema", "production_entrypoint"}


def test_contract_rejects_unsafe_logging_stream_routing(tmp_path) -> None:
    logging_config = _valid_logging_config()
    logging_config["handlers"]["stdout"]["stream"] = "ext://sys.stderr"
    _write(tmp_path, _valid_config(), logging_config=logging_config)

    failed = {item.code for item in validate_railway_contract(tmp_path) if not item.passed}

    assert failed == {"logging_config"}


def test_contract_rejects_numeric_strings_that_railway_schema_rejects(tmp_path) -> None:
    config = _valid_config()
    config["deploy"].update({
        "healthcheckTimeout": "300",
        "restartPolicyMaxRetries": "10",
        "overlapSeconds": "30",
        "drainingSeconds": "15",
    })
    _write(tmp_path, config)

    failed = {item.code for item in validate_railway_contract(tmp_path) if not item.passed}

    assert failed == {
        "healthcheck_timeout",
        "restart_retries",
        "graceful_handoff",
    }


def test_explicit_start_command_satisfies_entrypoint_without_procfile(tmp_path) -> None:
    config = _valid_config()
    (tmp_path / "railway.json").write_text(json.dumps(config), encoding="utf-8")
    (tmp_path / "logging.railway.json").write_text(
        json.dumps(_valid_logging_config()),
        encoding="utf-8",
    )

    findings = validate_railway_contract(tmp_path)

    assert all(item.passed for item in findings)


def test_procfile_cannot_replace_explicit_start_command(tmp_path) -> None:
    config = _valid_config()
    config["deploy"].pop("startCommand")
    _write(tmp_path, config)

    failed = {
        item.code for item in validate_railway_contract(tmp_path) if not item.passed
    }

    assert failed == {"production_entrypoint"}


def test_invalid_or_missing_file_returns_safe_single_finding(tmp_path) -> None:
    missing = validate_railway_contract(tmp_path)
    assert [(item.code, item.passed) for item in missing] == [("railway_file", False)]

    (tmp_path / "railway.json").write_text("{invalid", encoding="utf-8")
    invalid = validate_railway_contract(tmp_path)
    assert [(item.code, item.passed) for item in invalid] == [("railway_file", False)]


def test_restore_certification_profile_is_explicit_one_shot() -> None:
    config = json.loads(
        Path("railway.restore.certification.json").read_text(encoding="utf-8")
    )
    assert config["build"]["dockerfilePath"] == "Dockerfile.backup"
    deploy = config["deploy"]
    assert deploy["startCommand"] == "sh scripts/restore_certification_entrypoint.sh"
    assert deploy["restartPolicyType"] == "NEVER"
    assert "cronSchedule" not in deploy
    assert "healthcheckPath" not in deploy


def test_restore_certification_entrypoint_is_fail_closed() -> None:
    entrypoint = Path("scripts/restore_certification_entrypoint.sh").read_text(
        encoding="utf-8"
    )
    for required in (
        "RESTORE_ARTIFACT",
        "RESTORE_ALLOWED",
        "RESTORE_TARGET_CONFIRMATION",
        "ISOLATED_RESTORE_TARGET",
        "RESTORE_TARGET_DATABASE_URL",
        "RESTORE_SOURCE_DATABASE_URL",
        "python -m scripts.postgres_restore",
        "RESTORE_AMTHERO24",
    ):
        assert required in entrypoint
    assert "BACKUP_ENCRYPTION_KEY=" not in entrypoint
    assert "postgresql://" not in entrypoint
    subprocess.run(
        ["sh", "-n", "scripts/restore_certification_entrypoint.sh"],
        check=True,
        capture_output=True,
        text=True,
    )


def test_restore_cli_entrypoints_load_repository_dependencies() -> None:
    commands = (
        [sys.executable, "-m", "scripts.postgres_restore", "--help"],
        [sys.executable, "scripts/postgres_restore.py", "--help"],
    )
    for command in commands:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        assert "Restore and verify" in completed.stdout


def test_restore_rejects_source_target_identity_before_artifact_access(tmp_path) -> None:
    source = "postgres://db.internal/amthero24_restore"
    target = "postgresql://db.internal:5432/amthero24_restore"

    with pytest.raises(PermissionError, match="isolated from source"):
        restore_backup(
            target,
            tmp_path / "missing.dump.fernet",
            confirmation="RESTORE_AMTHERO24",
            restore_allowed=True,
            source_database_url=source,
        )
