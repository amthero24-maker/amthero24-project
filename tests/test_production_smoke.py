"""Tests for non-mutating production smoke checks."""
from __future__ import annotations

from unittest.mock import patch

import production_smoke


def _healthy(path: str) -> tuple[int, dict]:
    if path == "/health":
        return 200, {"status": "ok", "version": "3.2.0", "storage": "postgresql"}
    if path == "/ready":
        return 200, {
            "status": "ready",
            "components": {
                "storage_backend": "postgresql",
                "postgresql_schemas": "initialized",
                "database_fallback": "fail-closed",
                "process_lifecycle": "accepting",
                "webhook_signature": "enforced",
                "webhook_idempotency": "retry-safe",
                "durable_inbound_queue": "configured",
                "outbound_delivery_receipts": "enabled",
                "reminders": "enabled",
                "reminder_encryption": "configured",
                "admin_overview": "configured",
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
            expected_version="3.2.0",
            require_signature=True,
            require_launch_ready=True,
        )
    assert checks
    assert all(item.passed for item in checks)
    assert {item.name for item in checks} >= {
        "health",
        "readiness",
        "storage_backend",
        "postgresql_schemas",
        "database_fallback",
        "process_lifecycle",
        "webhook_idempotency",
        "durable_inbound_queue",
        "outbound_delivery_receipts",
        "reminders",
        "reminder_encryption",
        "admin_secret",
        "launch_decision",
    }


def test_smoke_fails_on_storage_signature_delivery_and_lifecycle_misconfiguration() -> None:
    def response(base: str, path: str, **kwargs):
        if path == "/health":
            return 200, {"status": "ok", "version": "3.2.0"}
        return 503, {
            "status": "not_ready",
            "components": {
                "storage_backend": "json-fallback",
                "postgresql_schemas": "unavailable",
                "database_fallback": "allowed",
                "process_lifecycle": "draining",
                "webhook_signature": "optional",
                "webhook_idempotency": "missing",
                "durable_inbound_queue": "misconfigured",
                "outbound_delivery_receipts": "missing",
                "reminders": "misconfigured",
                "reminder_encryption": "weak",
                "privacy_retention": "enabled",
                "provider_telemetry": "enabled",
                "abuse_guard": "enforced",
            },
        }

    with patch("production_smoke.fetch_json", side_effect=response):
        checks = production_smoke.run_smoke("https://example.test", require_signature=True)
    failed = {item.name for item in checks if not item.passed}
    assert failed == {
        "readiness",
        "storage_backend",
        "postgresql_schemas",
        "database_fallback",
        "process_lifecycle",
        "webhook_signature",
        "webhook_idempotency",
        "durable_inbound_queue",
        "outbound_delivery_receipts",
        "reminders",
        "reminder_encryption",
    }


def test_disabled_durable_queue_is_allowed_during_safe_rollout() -> None:
    def response(base: str, path: str, **kwargs):
        status, payload = _healthy(path)
        if path == "/ready":
            payload["components"]["durable_inbound_queue"] = "disabled"
        return status, payload

    with patch("production_smoke.fetch_json", side_effect=response):
        checks = production_smoke.run_smoke("https://example.test", require_signature=True)

    assert next(item for item in checks if item.name == "durable_inbound_queue").passed is True


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
