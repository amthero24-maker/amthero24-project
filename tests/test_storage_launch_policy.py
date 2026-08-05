"""Launch-gate coverage for durable storage split-brain prevention."""
from __future__ import annotations

from datetime import UTC, datetime

from launch_readiness import build_launch_report


def _overview() -> dict:
    return {
        "storage_backend": "postgresql",
        "messages_24h": {"total": 0, "failed": 0},
        "providers": {
            "groq": {"total": 0, "failure": 0, "circuit": "closed", "latency_ms": {}},
            "whatsapp": {"total": 0, "failure": 0, "latency_ms": {}},
        },
        "reminders": {"by_status": {}},
        "abuse_guard": {"active_blocks": 0},
        "entitlements": {"mode": "observe-only"},
    }


def _env() -> dict[str, str]:
    return {
        "META_APP_SECRET": "configured",
        "WEBHOOK_SIGNATURE_REQUIRED": "true",
        "ADMIN_API_TOKEN": "admin-token-2026-unique-8xK2mP7qR4vN",
        "PRIVACY_RETENTION_ENABLED": "true",
        "REMINDER_WORKER_ENABLED": "true",
        "REMINDER_CANARY_SENDERS": "+491701234567",
        "REMINDER_ENCRYPTION_KEY": "reminder-key-2026-unique-7fA9xQ2mLp8V",
        "REMINDER_LEGACY_TOKEN_DECRYPTION_ENABLED": "false",
        "WHATSAPP_REMINDER_TEMPLATE": "approved_template",
        "HUMAN_SUPPORT_ENABLED": "false",
    }


def test_fail_closed_database_policy_is_launch_ready() -> None:
    report = build_launch_report(
        _overview(), env=_env(), now=datetime(2026, 7, 26, 12, tzinfo=UTC)
    )
    checks = {item["code"]: item for item in report["checks"]}
    assert checks["database_fail_closed"]["status"] == "ready"
    assert report["status"] == "ready"


def test_explicit_json_fallback_blocks_controlled_beta() -> None:
    environment = _env()
    environment["DATABASE_FALLBACK_ALLOWED"] = "true"
    report = build_launch_report(
        _overview(), env=environment, now=datetime(2026, 7, 26, 12, tzinfo=UTC)
    )
    checks = {item["code"]: item for item in report["checks"]}
    assert checks["database_fail_closed"]["status"] == "blocked"
    assert report["status"] == "blocked"
    assert "DATABASE_FALLBACK_ALLOWED=false" in checks["database_fail_closed"]["action"]
