"""Tests for safe scheduled production monitoring."""
from __future__ import annotations

import json

from production_smoke import SmokeCheck
from scripts.production_monitor import MonitorReport, run_monitor, write_report


def _failed() -> list[SmokeCheck]:
    return [SmokeCheck("readiness", "fail", "HTTP 503; status=not_ready")]


def _healthy() -> list[SmokeCheck]:
    return [
        SmokeCheck("health", "pass", "HTTP 200; status=ok"),
        SmokeCheck("readiness", "pass", "HTTP 200; status=ready"),
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
