"""Beta launch gate tests using aggregate, privacy-safe inputs."""
from __future__ import annotations

from datetime import UTC, datetime

from launch_readiness import build_launch_report


STRONG_ADMIN_TOKEN = "admin-token-2026-unique-8xK2mP7qR4vN"
STRONG_REMINDER_KEY = "reminder-key-2026-unique-7fA9xQ2mLp8V"


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
        "ADMIN_API_TOKEN": STRONG_ADMIN_TOKEN,
        "PRIVACY_RETENTION_ENABLED": "true",
        "REMINDER_WORKER_ENABLED": "true",
        "REMINDER_ENCRYPTION_KEY": STRONG_REMINDER_KEY,
        "REMINDER_LEGACY_TOKEN_DECRYPTION_ENABLED": "false",
        "WHATSAPP_REMINDER_TEMPLATE": "amthero24_reminder",
        "HUMAN_SUPPORT_ENABLED": "false",
    }


def test_healthy_system_is_ready_for_controlled_beta() -> None:
    report = build_launch_report(
        _healthy_overview(), env=_healthy_env(), now=datetime(2026, 7, 26, 12, tzinfo=UTC)
    )
    assert report["status"] == "ready"
    assert report["summary"]["blocked"] == 0
    assert report["launch_scope"] == "controlled_beta"
    checks = {item["code"]: item for item in report["checks"]}
    assert checks["brief_scanner_runtime"]["status"] == "ready"
    assert checks["brief_scanner_runtime"]["detail"] == (
        "Brief Scanner runtime is safely disabled."
    )


def test_missing_security_and_database_block_launch() -> None:
    overview = _healthy_overview()
    overview["storage_backend"] = "json-fallback"
    report = build_launch_report(overview, env={}, now=datetime(2026, 7, 26, 12, tzinfo=UTC))
    codes = {item["code"]: item["status"] for item in report["checks"]}
    assert report["status"] == "blocked"
    assert codes["postgresql"] == "blocked"
    assert codes["meta_signature"] == "blocked"
    assert codes["admin_access"] == "blocked"
    assert codes["reminder_encryption"] == "warning"


def test_provider_outage_blocks_launch_and_high_latency_warns() -> None:
    overview = _healthy_overview()
    overview["providers"]["groq"].update({"failure": 60, "success": 40, "circuit": "open"})
    overview["providers"]["whatsapp"]["latency_ms"]["p95"] = 20_000
    report = build_launch_report(overview, env=_healthy_env(), now=datetime(2026, 7, 26, 12, tzinfo=UTC))
    statuses = {item["code"]: item["status"] for item in report["checks"]}
    assert statuses["provider_groq"] == "blocked"
    assert statuses["provider_whatsapp"] == "warning"


def test_weak_reminder_or_enabled_support_secrets_block_launch() -> None:
    environment = _healthy_env()
    environment["REMINDER_ENCRYPTION_KEY"] = "weak"
    environment["HUMAN_SUPPORT_ENABLED"] = "true"
    environment["SUPPORT_ENCRYPTION_KEY"] = "weak"
    environment["SUPPORT_API_TOKEN"] = "weak"
    report = build_launch_report(_healthy_overview(), env=environment)
    statuses = {item["code"]: item["status"] for item in report["checks"]}
    assert statuses["reminder_encryption"] == "blocked"
    assert statuses["human_support_security"] == "blocked"
    assert report["status"] == "blocked"


def test_legacy_reminder_compatibility_is_visible_warning() -> None:
    environment = _healthy_env()
    environment["REMINDER_LEGACY_TOKEN_DECRYPTION_ENABLED"] = "true"
    report = build_launch_report(_healthy_overview(), env=environment)
    checks = {item["code"]: item for item in report["checks"]}
    assert checks["reminder_legacy_decryption"]["status"] == "warning"
    assert report["status"] == "warning"


def test_unsafe_brief_scanner_runtime_configuration_blocks_launch_without_leak() -> None:
    environment = _healthy_env()
    sensitive_value = "synthetic-sensitive-invalid-runtime-value"
    environment["BRIEF_SCANNER_RUNTIME_ENABLED"] = sensitive_value

    report = build_launch_report(_healthy_overview(), env=environment)
    checks = {item["code"]: item for item in report["checks"]}

    assert checks["brief_scanner_runtime"]["status"] == "blocked"
    assert "brief_scanner_runtime_flag_invalid" in checks["brief_scanner_runtime"]["detail"]
    assert report["status"] == "blocked"
    assert sensitive_value not in str(report)


def test_supported_brief_scanner_runtime_configuration_is_launch_ready() -> None:
    environment = _healthy_env()
    environment.update(
        {
            "BRIEF_SCANNER_RUNTIME_ENABLED": "true",
            "BRIEF_SCANNER_RUNTIME_MISSION_ENABLED": "true",
            "BRIEF_SCANNER_RUNTIME_REMINDER_ENABLED": "true",
        }
    )

    report = build_launch_report(_healthy_overview(), env=environment)
    checks = {item["code"]: item for item in report["checks"]}

    assert checks["brief_scanner_runtime"] == {
        "code": "brief_scanner_runtime",
        "status": "ready",
        "detail": (
            "Brief Scanner runtime configuration is ready for "
            "create_mission, create_reminder."
        ),
    }
    assert report["status"] == "ready"


def test_unsupported_draft_runtime_configuration_blocks_launch() -> None:
    environment = _healthy_env()
    environment.update(
        {
            "BRIEF_SCANNER_RUNTIME_ENABLED": "true",
            "BRIEF_SCANNER_RUNTIME_MISSION_ENABLED": "true",
            "BRIEF_SCANNER_RUNTIME_DRAFT_ENABLED": "true",
        }
    )

    report = build_launch_report(_healthy_overview(), env=environment)
    checks = {item["code"]: item for item in report["checks"]}

    assert checks["brief_scanner_runtime"]["status"] == "blocked"
    assert "brief_scanner_runtime_draft_unsupported" in (
        checks["brief_scanner_runtime"]["detail"]
    )
    assert report["status"] == "blocked"


def test_report_contains_no_user_content() -> None:
    report = build_launch_report(_healthy_overview(), env=_healthy_env())
    serialized = str(report)
    for forbidden in ("49123", "وسام", "first_name", "phone_hash", "message text"):
        assert forbidden not in serialized
