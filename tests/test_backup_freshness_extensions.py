"""Composition tests for backup freshness admin and launch integration."""
from __future__ import annotations

from unittest.mock import patch

import backup_freshness_extensions as layer


def test_final_overview_is_rechecked_after_backup_metrics_are_added() -> None:
    base = {"status": "ok", "users": {"total": 0}}
    backups = {
        "state": "missing",
        "last_status": "never",
        "last_success_at": None,
        "last_attempt_at": None,
        "age_hours": None,
        "encrypted": False,
        "schema_version": 0,
        "artifact_size_bytes": 0,
        "last_failure_code": "",
    }

    with patch.object(layer, "_ORIGINAL_ADMIN_BUILD_OVERVIEW", return_value=base.copy()), patch.object(
        layer, "aggregate_backup_freshness", return_value=backups
    ), patch.object(layer, "assert_no_personal_fields") as guard:
        payload = layer._build_overview(object())

    assert payload["backups"] == backups
    guard.assert_called_once_with(payload)


def test_launch_composition_adds_exactly_one_backup_check() -> None:
    base_report = {
        "status": "ready",
        "checks": [{"code": "storage_backend", "status": "ready", "detail": "ok"}],
        "summary": {"ready": 1, "warning": 0, "blocked": 0},
        "next_actions": [],
    }
    overview = {
        "backups": {
            "state": "missing",
            "last_status": "never",
            "age_hours": None,
            "encrypted": False,
            "schema_version": 0,
        }
    }

    with patch.object(layer, "_ORIGINAL_BUILD_LAUNCH_REPORT", return_value=base_report):
        first = layer._build_launch_report(overview, env={})
        second = layer._build_launch_report(overview, env={})

    for report in (first, second):
        checks = [item for item in report["checks"] if item["code"] == "backup_freshness"]
        assert len(checks) == 1
        assert checks[0]["status"] == "warning"
