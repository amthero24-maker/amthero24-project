"""Tests for non-mutating production smoke checks."""
from __future__ import annotations

from unittest.mock import patch

import production_smoke


def _healthy(path: str) -> tuple[int, dict]:
    if path == "/health":
        return 200, {"status": "ok", "version": "3.0.0", "storage": "postgresql"}
    if path == "/ready":
        return 200, {
            "status": "ready",
            "components": {
                "storage_backend": "postgresql",
                "webhook_signature": "enforced",
                "privacy_retention": "enabled",
                "provider_telemetry": "enabled",
                "abuse_guard": "enforced",
            },
        }
    return 200, {"status": "ready"}


def test_smoke_passes_for_healthy_production() -> None:
    with patch("production_smoke.fetch_json", side_effect=lambda base, path, **kwargs: _healthy(path)):
        checks = production_smoke.run_smoke(
            "https://example.test",
            admin_token="secret",
            expected_version="3.0.0",
            require_signature=True,
            require_launch_ready=True,
        )
    assert checks
    assert all(item.passed for item in checks)
    assert {item.name for item in checks} >= {"health", "readiness", "storage_backend", "launch_decision"}


def test_smoke_fails_on_json_fallback_and_optional_signature() -> None:
    def response(base: str, path: str, **kwargs):
        if path == "/health":
            return 200, {"status": "ok", "version": "3.0.0"}
        return 200, {
            "status": "ready",
            "components": {
                "storage_backend": "json-fallback",
                "webhook_signature": "optional",
                "privacy_retention": "enabled",
                "provider_telemetry": "enabled",
                "abuse_guard": "enforced",
            },
        }

    with patch("production_smoke.fetch_json", side_effect=response):
        checks = production_smoke.run_smoke("https://example.test", require_signature=True)
    failed = {item.name for item in checks if not item.passed}
    assert failed == {"storage_backend", "webhook_signature"}


def test_smoke_stops_cleanly_when_health_is_unavailable() -> None:
    with patch("production_smoke.fetch_json", side_effect=production_smoke.SmokeError("endpoint unavailable")):
        checks = production_smoke.run_smoke("https://example.test")
    assert len(checks) == 1
    assert checks[0].name == "health"
    assert checks[0].passed is False


def test_launch_warning_is_allowed_unless_strict_gate_requested() -> None:
    def response(base: str, path: str, **kwargs):
        if path == "/admin/launch-readiness":
            return 200, {"status": "warning"}
        return _healthy(path)

    with patch("production_smoke.fetch_json", side_effect=response):
        relaxed = production_smoke.run_smoke("https://example.test", admin_token="secret")
        strict = production_smoke.run_smoke(
            "https://example.test",
            admin_token="secret",
            require_launch_ready=True,
        )
    assert next(item for item in relaxed if item.name == "launch_decision").passed is True
    assert next(item for item in strict if item.name == "launch_decision").passed is False
