"""Launch-gate coverage for durable storage split-brain prevention."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from launch_readiness import build_launch_report


NOW = datetime(2026, 7, 26, 12, tzinfo=UTC)


def _overview() -> dict:
    return {
        "storage_backend": "postgresql",
        "backup_recovery": {
            "receipt": "recent_success",
            "latest_outcome": "success",
            "latest_event_at": (NOW - timedelta(hours=1)).isoformat(),
            "age_seconds": 3600,
            "max_age_hours": 30,
        },
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
        "PRODUCTION_BACKUP_RESTORE_CERTIFIED": "true",
        "PRODUCTION_BACKUP_RESTORE_CERTIFIED_AT": (
            NOW - timedelta(minutes=30)
        ).isoformat(),
    }


def test_fail_closed_database_and_backup_recovery_are_launch_ready() -> None:
    report = build_launch_report(
        _overview(), env=_env(), now=NOW
    )
    checks = {item["code"]: item for item in report["checks"]}
    assert checks["database_fail_closed"]["status"] == "ready"
    assert checks["production_backup_recovery"]["status"] == "ready"
    assert report["status"] == "ready"


def test_fail_closed_database_alone_does_not_bypass_backup_recovery() -> None:
    overview = _overview()
    overview.pop("backup_recovery")

    report = build_launch_report(
        overview,
        env=_env(),
        now=NOW,
    )
    checks = {item["code"]: item for item in report["checks"]}

    assert checks["database_fail_closed"]["status"] == "ready"
    assert checks["production_backup_recovery"]["status"] == "blocked"
    assert "missing" in checks["production_backup_recovery"]["detail"]
    assert report["status"] == "blocked"


def test_explicit_json_fallback_blocks_controlled_beta() -> None:
    environment = _env()
    environment["DATABASE_FALLBACK_ALLOWED"] = "true"
    report = build_launch_report(
        _overview(), env=environment, now=NOW
    )
    checks = {item["code"]: item for item in report["checks"]}
    assert checks["database_fail_closed"]["status"] == "blocked"
    assert report["status"] == "blocked"
    assert "DATABASE_FALLBACK_ALLOWED=false" in checks["database_fail_closed"]["action"]
