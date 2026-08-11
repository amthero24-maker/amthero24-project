"""Privacy-safe diagnostics tests for production launch smoke checks."""
from __future__ import annotations

from unittest.mock import patch

import production_smoke


def _response(path: str) -> tuple[int, dict]:
    if path == "/health":
        return 200, {"status": "ok"}
    if path == "/ready":
        return 200, {
            "status": "ready",
            "components": {
                "storage_backend": "postgresql",
                "postgresql_schemas": "initialized",
                "database_schema_migrations": "current",
                "database_schema_version": 2,
                "database_fallback": "fail-closed",
                "process_lifecycle": "accepting",
                "webhook_signature": "enforced",
                "webhook_idempotency": "retry-safe",
                "durable_inbound_queue": "configured",
                "outbound_delivery_receipts": "enabled",
                "reminders": "enabled",
                "reminder_worker": "running",
                "reminder_encryption": "configured",
                "admin_overview": "configured",
                "privacy_retention": "enabled",
                "provider_telemetry": "enabled",
                "abuse_guard": "enforced",
            },
        }
    if path == "/admin/launch-readiness":
        return 200, {
            "status": "warning",
            "checks": [
                {"code": "postgresql", "status": "ready", "detail": "safe"},
                {
                    "code": "provider_groq",
                    "status": "warning",
                    "detail": "do-not-expose-detail",
                    "action": "do-not-expose-action",
                },
                {
                    "code": "provider whatsapp !!",
                    "status": "blocked",
                    "detail": "another-secret-detail",
                },
            ],
        }
    if path == "/admin/overview":
        return 200, {"reminders": {"total": 0, "by_status": {}}}
    raise AssertionError(path)


def test_launch_decision_exposes_only_bounded_non_ready_codes() -> None:
    with patch(
        "production_smoke.fetch_json",
        side_effect=lambda base, path, **kwargs: _response(path),
    ):
        checks = production_smoke.run_smoke(
            "https://example.test",
            admin_token="secret",
            require_signature=True,
            require_launch_ready=True,
        )

    decision = next(item for item in checks if item.name == "launch_decision")
    assert decision.passed is False
    assert decision.detail == (
        "warning; non_ready=warning:provider_groq,blocked:provider_whatsapp"
    )
    assert "do-not-expose" not in decision.detail
    assert "another-secret-detail" not in decision.detail


def test_launch_diagnostic_falls_back_to_top_level_decision_without_checks() -> None:
    assert production_smoke._launch_decision_detail({"status": "warning"}) == "warning"
