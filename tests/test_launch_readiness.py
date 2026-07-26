"""Beta launch gate tests using aggregate, privacy-safe inputs."""
from __future__ import annotations

from datetime import UTC, datetime

from launch_readiness import build_launch_report


def _healthy_overview() -> dict:
    return {
        "storage_backend": "postgresql",
        "messages_24h": {"total": 100, "failed": 1},
        "providers": {
            "groq": {"total": 100, "success": 99, "failure": 1, "circuit_rejected": 0, "circuit": "closed", "latency_ms": {"p95": 900}},
            "whatsapp": {"total": 100, "success": 99, "failure": 1, "circuit_rejected": 0, "latency_ms": {"p95": 500}},
        },
        "reminders": {"by_status": {"pending": 2, "sent": 5}},
        "abuse_guard": {"active_blocks": 0},
        "entitlements": {"mode": "observe-only"},
    }


def _healthy_env() -> dict[str, str]:
    return {
        "META_APP_SECRET": "configured",
        "WEBHOOK_SIGNATURE_REQUIRED": "true",
        "ADMIN_API_TOKEN": "configured",
        "PRIVACY_RETENTION_ENABLED": "true",
        "REMINDER_WORKER_ENABLED": "true",
        "WHATSAPP_REMINDER_TEMPLATE": "amthero24_reminder",
    }


def test_healthy_system_is_ready_for_controlled_beta() -> None:
    report = build_launch_report(
        _healthy_overview(), env=_healthy_env(), now=datetime(2026, 7, 26, 12, tzinfo=UTC)
    )
    assert report["status"] == "ready"
    assert report["summary"]["blocked"] == 0
    assert report["launch_scope"] == "controlled_beta"


def test_missing_security_and_database_block_launch() -> None:
    overview = _healthy_overview()
    overview["storage_backend"] = "json-fallback"
    report = build_launch_report(overview, env={}, now=datetime(2026, 7, 26, 12, tzinfo=UTC))
    codes = {item["code"]: item["status"] for item in report["checks"]}
    assert report["status"] == "blocked"
    assert codes["postgresql"] == "blocked"
    assert codes["meta_signature"] == "blocked"
    assert codes["admin_access"] == "blocked"


def test_provider_outage_blocks_launch_and_high_latency_warns() -> None:
    overview = _healthy_overview()
    overview["providers"]["groq"].update({"failure": 60, "success": 40, "circuit": "open"})
    overview["providers"]["whatsapp"]["latency_ms"]["p95"] = 20_000
    report = build_launch_report(overview, env=_healthy_env(), now=datetime(2026, 7, 26, 12, tzinfo=UTC))
    statuses = {item["code"]: item["status"] for item in report["checks"]}
    assert statuses["provider_groq"] == "blocked"
    assert statuses["provider_whatsapp"] == "warning"


def test_report_contains_no_user_content() -> None:
    report = build_launch_report(_healthy_overview(), env=_healthy_env())
    serialized = str(report)
    for forbidden in ("49123", "وسام", "first_name", "phone_hash", "message text"):
        assert forbidden not in serialized
