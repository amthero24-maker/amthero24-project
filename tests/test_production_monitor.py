"""Tests for safe scheduled production monitoring."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from production_smoke import SmokeCheck
from scripts.production_monitor import MonitorReport, run_monitor, write_report


_ROOT = Path(__file__).resolve().parents[1]


def _failed() -> list[SmokeCheck]:
    return [SmokeCheck("readiness", "fail", "HTTP 503; status=not_ready")]


def _healthy() -> list[SmokeCheck]:
    return [
        SmokeCheck("health", "pass", "HTTP 200; status=ok"),
        SmokeCheck("readiness", "pass", "HTTP 200; status=ready"),
    ]


def _outbound_warning() -> list[SmokeCheck]:
    return [
        SmokeCheck("health", "pass", "HTTP 200; status=ok"),
        SmokeCheck(
            "launch_decision",
            "fail",
            "warning; non_ready=warning:outbound_delivery",
        ),
    ]


def test_monitor_retries_and_recovers_without_extra_attempts() -> None:
    calls: list[dict] = []
    sleeps: list[float] = []

    def runner(*args, **kwargs):
        calls.append(kwargs)
        return _failed() if len(calls) == 1 else _healthy()

    report = run_monitor(
        "https://production.example",
        admin_token="super-secret-token",
        expected_version="3.4.0",
        attempts=4,
        delay_seconds=2,
        sleep=sleeps.append,
        smoke_runner=runner,
    )

    assert report.status == "healthy"
    assert report.attempts_run == 2
    assert report.recovered_after_retry is True
    assert sleeps == [2.0]
    assert len(calls) == 2
    assert all(call["require_postgresql"] is True for call in calls)
    assert all(call["require_signature"] is True for call in calls)


def test_monitor_reports_final_failure_after_bounded_retries() -> None:
    attempts = 0

    def runner(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        return _failed()

    report = run_monitor(
        "https://production.example",
        attempts=99,
        delay_seconds=0,
        smoke_runner=runner,
    )

    assert report.status == "unhealthy"
    assert report.attempts_configured == 5
    assert report.attempts_run == 5
    assert attempts == 5
    assert report.recovered_after_retry is False


def test_monitor_converts_runner_exception_to_safe_failure() -> None:
    def runner(*args, **kwargs):
        raise RuntimeError("https://private.example?token=secret")

    report = run_monitor(
        "https://production.example",
        admin_token="secret-admin-token",
        attempts=1,
        delay_seconds=0,
        smoke_runner=runner,
    )
    payload = write_report(report)

    assert report.status == "unhealthy"
    assert "RuntimeError" in payload
    assert "private.example" not in payload
    assert "secret-admin-token" not in payload
    assert "production.example" not in payload


def test_outbound_warning_adds_only_aggregate_privacy_safe_diagnostics() -> None:
    overview = {
        "outbound_delivery": {
            "tracked_24h": 7,
            "by_status": {
                "accepted": 0,
                "sent": 1,
                "delivered": 3,
                "read": 2,
                "failed": 1,
            },
            "failure_codes": {"131047": 1},
            "terminal_24h": 6,
            "delivery_success_pct": 83.3,
            "pending_over_15m": 1,
            "oldest_pending_age_seconds": 1200,
            "recovery_required": True,
            "recovery_evidence": "unresolved_failure",
            "recovery_failure_code": "131031",
            "phone": "+49123456789",
            "message": "must never appear",
        }
    }
    with patch(
        "scripts.production_monitor.fetch_json",
        return_value=(200, overview),
    ) as fetch:
        report = run_monitor(
            "https://production.example",
            admin_token="super-secret-token",
            attempts=1,
            delay_seconds=0,
            smoke_runner=lambda *args, **kwargs: _outbound_warning(),
        )

    diagnostic = next(
        item for item in report.checks
        if item["name"] == "outbound_delivery_diagnostics"
    )
    assert diagnostic["status"] == "pass"
    assert diagnostic["detail"] == (
        "tracked_24h=7; by_status=accepted:0,sent:1,delivered:3,read:2,failed:1; "
        "failure_codes=131047:1; terminal_24h=6; delivery_success_pct=83.3; "
        "pending_over_15m=1; oldest_pending_age_seconds=1200; "
        "recovery_required=true; recovery_evidence=unresolved_failure; "
        "recovery_failure_code=131031"
    )
    assert "+49123456789" not in write_report(report)
    assert "must never appear" not in write_report(report)
    assert "super-secret-token" not in write_report(report)
    fetch.assert_called_once()


def test_outbound_diagnostics_default_to_no_failure_codes_or_recovery() -> None:
    overview = {
        "outbound_delivery": {
            "tracked_24h": 1,
            "by_status": {"failed": 1},
            "terminal_24h": 1,
            "delivery_success_pct": 0,
            "pending_over_15m": 0,
            "oldest_pending_age_seconds": 0,
        }
    }
    with patch("scripts.production_monitor.fetch_json", return_value=(200, overview)):
        report = run_monitor(
            "https://production.example",
            admin_token="secret",
            attempts=1,
            delay_seconds=0,
            smoke_runner=lambda *args, **kwargs: _outbound_warning(),
        )
    diagnostic = next(item for item in report.checks if item["name"] == "outbound_delivery_diagnostics")
    assert "failure_codes=none" in diagnostic["detail"]
    assert "recovery_required=false" in diagnostic["detail"]
    assert "recovery_evidence=none" in diagnostic["detail"]
    assert "recovery_failure_code=none" in diagnostic["detail"]


def test_outbound_diagnostics_are_not_fetched_without_matching_launch_warning() -> None:
    with patch("scripts.production_monitor.fetch_json") as fetch:
        report = run_monitor(
            "https://production.example",
            admin_token="secret",
            attempts=1,
            delay_seconds=0,
            smoke_runner=lambda *args, **kwargs: _failed(),
        )

    assert report.status == "unhealthy"
    assert all(item["name"] != "outbound_delivery_diagnostics" for item in report.checks)
    fetch.assert_not_called()


def test_written_report_contains_only_incident_safe_fields(tmp_path) -> None:
    report = MonitorReport(
        status="healthy",
        generated_at="2026-07-26T12:00:00+00:00",
        attempts_configured=3,
        attempts_run=1,
        recovered_after_retry=False,
        checks=[{"name": "health", "status": "pass", "detail": "HTTP 200; status=ok"}],
    )
    output = tmp_path / "report.json"

    encoded = write_report(report, output)
    parsed = json.loads(encoded)

    assert json.loads(output.read_text(encoding="utf-8")) == parsed
    assert set(parsed) == {
        "status",
        "generated_at",
        "attempts_configured",
        "attempts_run",
        "recovered_after_retry",
        "checks",
    }


def test_workflow_does_not_skip_when_production_url_is_missing() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "production-smoke.yml").read_text(
        encoding="utf-8"
    )

    assert "if: ${{ vars.PRODUCTION_BASE_URL != '' }}" not in workflow
    assert "python scripts/production_monitor.py --output production-monitor.json" in workflow
    assert "if: steps.monitor.outcome == 'failure'" in workflow
