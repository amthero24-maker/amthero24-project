"""Tests for deterministic Railway configuration safety."""
from __future__ import annotations

import json

from railway_contract import report_payload, validate_railway_contract


def _write(root, config, procfile="web: uvicorn webhook_security:app --host 0.0.0.0 --port $PORT\n"):
    (root / "railway.json").write_text(json.dumps(config), encoding="utf-8")
    (root / "Procfile").write_text(procfile, encoding="utf-8")


def _valid_config():
    return {
        "$schema": "https://railway.com/railway.schema.json",
        "deploy": {
            "startCommand": "uvicorn webhook_security:app --host 0.0.0.0 --port $PORT",
            "healthcheckPath": "/ready",
            "healthcheckTimeout": 300,
            "restartPolicyType": "ON_FAILURE",
            "restartPolicyMaxRetries": 10,
            "overlapSeconds": "30",
            "drainingSeconds": "15",
        },
    }


def test_repository_railway_contract_passes() -> None:
    findings = validate_railway_contract(".")
    assert findings
    assert all(item.passed for item in findings)
    assert report_payload(findings)["passed"] is True


def test_contract_rejects_liveness_path_and_unsafe_handoff(tmp_path) -> None:
    config = _valid_config()
    config["deploy"].update({
        "healthcheckPath": "/health",
        "healthcheckTimeout": 10,
        "restartPolicyType": "NEVER",
        "restartPolicyMaxRetries": 0,
        "overlapSeconds": "5",
        "drainingSeconds": "30",
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


def test_explicit_start_command_satisfies_entrypoint_without_procfile(tmp_path) -> None:
    config = _valid_config()
    (tmp_path / "railway.json").write_text(json.dumps(config), encoding="utf-8")

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
