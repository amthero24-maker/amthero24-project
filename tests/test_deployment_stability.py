"""Tests for strict consecutive post-deployment certification."""
from __future__ import annotations

import json

from production_smoke import SmokeCheck
from scripts.deployment_stability import run_stability_gate, write_report


def _healthy():
    return [
        SmokeCheck("health", "pass", "HTTP 200; status=ok"),
        SmokeCheck("version", "pass", "expected=4.3.0; actual=4.3.0"),
        SmokeCheck("readiness", "pass", "HTTP 200; status=ready"),
    ]


def _surface_healthy():
    return [
        SmokeCheck("crawler_policy", "pass", "HTTP 200; disallow_all=yes"),
        SmokeCheck("framework_discovery", "pass", "blocked=3/3"),
        SmokeCheck("global_noindex", "pass", "HTTP 200; noindex=yes"),
    ]


def _failed():
    return [SmokeCheck("readiness", "fail", "HTTP 503; status=not_ready")]


def test_gate_requires_every_consecutive_sample() -> None:
    calls = []
    sleeps = []

    def runner(*args, **kwargs):
        calls.append(kwargs)
        return _healthy()

    report = run_stability_gate(
        "https://production.example",
        admin_token="secret",
        expected_version="4.3.0",
        samples=3,
        delay_seconds=2,
        sleep=sleeps.append,
        smoke_runner=runner,
        surface_runner=lambda *args, **kwargs: _surface_healthy(),
    )

    assert report.status == "stable"
    assert report.samples_run == 3
    assert report.consecutive_passes == 3
    assert sleeps == [2.0, 2.0]
    assert len(calls) == 3
    assert all(call["require_postgresql"] is True for call in calls)
    assert all(call["require_signature"] is True for call in calls)
    assert all(call["require_launch_ready"] is True for call in calls)
    assert all(
        any(check["name"] == "framework_discovery" for check in sample.checks)
        for sample in report.samples
    )


def test_gate_rejects_transient_failure_without_recovery_credit() -> None:
    calls = 0

    def runner(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _healthy() if calls == 1 else _failed()

    report = run_stability_gate(
        "https://production.example",
        admin_token="secret",
        expected_version="4.3.0",
        samples=5,
        delay_seconds=0,
        smoke_runner=runner,
        surface_runner=lambda *args, **kwargs: _surface_healthy(),
    )

    assert report.status == "unstable"
    assert report.samples_run == 2
    assert report.consecutive_passes == 1
    assert calls == 2


def test_gate_rejects_exposed_framework_discovery_immediately() -> None:
    report = run_stability_gate(
        "https://production.example",
        admin_token="secret",
        expected_version="4.3.0",
        samples=3,
        delay_seconds=0,
        smoke_runner=lambda *args, **kwargs: _healthy(),
        surface_runner=lambda *args, **kwargs: [
            SmokeCheck("framework_discovery", "fail", "blocked=2/3")
        ],
    )

    assert report.status == "unstable"
    assert report.samples_run == 1
    assert report.consecutive_passes == 0
    assert report.samples[0].checks[-1]["name"] == "framework_discovery"


def test_gate_bounds_samples_and_stops_on_exception() -> None:
    def runner(*args, **kwargs):
        raise RuntimeError("https://private.example?token=secret")

    report = run_stability_gate(
        "https://production.example",
        admin_token="secret-admin-token",
        expected_version="4.3.0",
        samples=99,
        delay_seconds=0,
        smoke_runner=runner,
        surface_runner=lambda *args, **kwargs: _surface_healthy(),
    )
    encoded = write_report(report)

    assert report.samples_configured == 6
    assert report.samples_run == 1
    assert report.status == "unstable"
    assert "RuntimeError" in encoded
    assert "private.example" not in encoded
    assert "secret-admin-token" not in encoded
    assert "production.example" not in encoded


def test_gate_requires_release_identity_without_network_calls() -> None:
    called = False

    def runner(*args, **kwargs):
        nonlocal called
        called = True
        return _healthy()

    missing_url = run_stability_gate(
        "",
        admin_token="secret",
        expected_version="4.3.0",
        delay_seconds=0,
        smoke_runner=runner,
        surface_runner=lambda *args, **kwargs: _surface_healthy(),
    )
    missing_token = run_stability_gate(
        "https://production.example",
        admin_token="",
        expected_version="4.3.0",
        delay_seconds=0,
        smoke_runner=runner,
        surface_runner=lambda *args, **kwargs: _surface_healthy(),
    )
    missing_version = run_stability_gate(
        "https://production.example",
        admin_token="secret",
        expected_version="",
        delay_seconds=0,
        smoke_runner=runner,
        surface_runner=lambda *args, **kwargs: _surface_healthy(),
    )

    assert called is False
    assert missing_url.samples[0].checks[0]["name"] == "base_url"
    assert missing_token.samples[0].checks[0]["name"] == "admin_token"
    assert missing_version.samples[0].checks[0]["name"] == "expected_version"


def test_written_report_has_only_safe_schema(tmp_path) -> None:
    report = run_stability_gate(
        "https://production.example",
        admin_token="secret",
        expected_version="4.3.0",
        samples=2,
        delay_seconds=0,
        smoke_runner=lambda *args, **kwargs: _healthy(),
        surface_runner=lambda *args, **kwargs: _surface_healthy(),
    )
    target = tmp_path / "deployment-stability.json"
    encoded = write_report(report, target)
    payload = json.loads(encoded)

    assert json.loads(target.read_text(encoding="utf-8")) == payload
    assert set(payload) == {
        "status",
        "generated_at",
        "samples_configured",
        "samples_run",
        "consecutive_passes",
        "samples",
    }
