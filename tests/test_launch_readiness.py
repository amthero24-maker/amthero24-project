"""Beta launch gate tests using aggregate, privacy-safe inputs."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import launch_readiness
import recovery_pipeline_certification as certification
from launch_readiness import build_launch_report
from recovery_pipeline_certification import RecoveryPipelineAssessment


STRONG_ADMIN_TOKEN = "admin-token-2026-unique-8xK2mP7qR4vN"
STRONG_REMINDER_KEY = "reminder-key-2026-unique-7fA9xQ2mLp8V"
NOW = datetime(2026, 7, 26, 12, tzinfo=UTC)


def _healthy_overview() -> dict:
    return {
        "storage_backend": "postgresql",
        "backup_recovery": {
            "receipt": "recent_success",
            "latest_outcome": "success",
            "latest_event_at": (NOW - timedelta(hours=1)).isoformat(),
            "age_seconds": 3600,
            "max_age_hours": 30,
        },
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
        "REMINDER_CANARY_SENDERS": "+491701234567",
        "REMINDER_ENCRYPTION_KEY": STRONG_REMINDER_KEY,
        "REMINDER_LEGACY_TOKEN_DECRYPTION_ENABLED": "false",
        "WHATSAPP_REMINDER_TEMPLATE": "amthero24_reminder",
        "HUMAN_SUPPORT_ENABLED": "false",
        "PRODUCTION_BACKUP_RESTORE_CERTIFICATION_MAX_AGE_HOURS": "168",
        "PRODUCTION_BACKUP_RESTORE_CERTIFIED": "true",
        "PRODUCTION_BACKUP_RESTORE_CERTIFIED_AT": (
            NOW - timedelta(minutes=30)
        ).isoformat(),
    }


def test_healthy_system_is_ready_for_controlled_beta() -> None:
    report = build_launch_report(
        _healthy_overview(), env=_healthy_env(), now=NOW
    )
    assert report["status"] == "ready"
    assert report["summary"]["blocked"] == 0
    assert report["launch_scope"] == "controlled_beta"
    checks = {item["code"]: item for item in report["checks"]}
    assert checks["brief_scanner_runtime"]["status"] == "ready"
    assert checks["brief_scanner_runtime"]["detail"] == (
        "Brief Scanner runtime is safely disabled."
    )
    assert checks["production_backup_recovery"]["status"] == "ready"


def test_missing_security_and_database_block_launch() -> None:
    overview = _healthy_overview()
    overview["storage_backend"] = "json-fallback"
    report = build_launch_report(overview, env={}, now=NOW)
    codes = {item["code"]: item["status"] for item in report["checks"]}
    assert report["status"] == "blocked"
    assert codes["postgresql"] == "blocked"
    assert codes["meta_signature"] == "blocked"
    assert codes["admin_access"] == "blocked"
    assert codes["reminder_encryption"] == "warning"
    assert codes["production_backup_recovery"] == "blocked"


@pytest.mark.parametrize(
    "receipt",
    (
        "missing",
        "latest_started",
        "latest_failure",
        "stale_success",
        "future_timestamp",
    ),
)
def test_backup_receipt_must_be_recent_success(receipt: str) -> None:
    overview = _healthy_overview()
    overview["backup_recovery"] = {
        "receipt": receipt,
        "latest_outcome": "failure",
        "latest_event_at": (NOW - timedelta(minutes=2)).isoformat(),
        "age_seconds": 120,
        "max_age_hours": 30,
        "private_path": "/backups/must-not-leak.dump",
    }

    report = build_launch_report(overview, env=_healthy_env(), now=NOW)
    check = next(
        item for item in report["checks"]
        if item["code"] == "production_backup_recovery"
    )

    assert report["status"] == "blocked"
    assert check["status"] == "blocked"
    assert receipt in check["detail"]
    assert "/backups" not in str(report)
    assert "must-not-leak" not in str(report)


def test_recent_backup_without_exact_event_time_blocks_launch() -> None:
    for value in ("", "not-a-time", "2026-07-26T11:00:00"):
        overview = _healthy_overview()
        overview["backup_recovery"]["latest_event_at"] = value

        report = build_launch_report(overview, env=_healthy_env(), now=NOW)
        check = next(
            item for item in report["checks"]
            if item["code"] == "production_backup_recovery"
        )

        assert report["status"] == "blocked"
        assert check["status"] == "blocked"
        assert "event time" in check["detail"].casefold()
        if value:
            assert value not in str(report)


def test_future_backup_event_time_blocks_launch() -> None:
    overview = _healthy_overview()
    overview["backup_recovery"]["latest_event_at"] = (
        NOW + timedelta(seconds=1)
    ).isoformat()

    report = build_launch_report(overview, env=_healthy_env(), now=NOW)
    check = next(
        item for item in report["checks"]
        if item["code"] == "production_backup_recovery"
    )

    assert report["status"] == "blocked"
    assert check["status"] == "blocked"
    assert "future" in check["detail"].casefold()
    assert overview["backup_recovery"]["latest_event_at"] not in str(report)


def test_recent_backup_without_restore_certification_blocks_launch() -> None:
    environment = _healthy_env()
    environment["PRODUCTION_BACKUP_RESTORE_CERTIFIED"] = "false"

    report = build_launch_report(_healthy_overview(), env=environment, now=NOW)
    checks = {item["code"]: item for item in report["checks"]}

    assert report["status"] == "blocked"
    assert checks["production_backup_recovery"]["status"] == "blocked"
    assert "restore certification" in checks["production_backup_recovery"]["detail"].casefold()


def test_restore_certification_requires_valid_utc_completion_time() -> None:
    for value in ("", "not-a-time", "2026-07-26T11:30:00"):
        environment = _healthy_env()
        environment["PRODUCTION_BACKUP_RESTORE_CERTIFIED_AT"] = value

        report = build_launch_report(_healthy_overview(), env=environment, now=NOW)
        check = next(
            item for item in report["checks"]
            if item["code"] == "production_backup_recovery"
        )

        assert report["status"] == "blocked"
        assert check["status"] == "blocked"
        assert "time" in check["detail"].casefold()
        if value:
            assert value not in str(report)


def test_future_restore_certification_time_blocks_launch() -> None:
    environment = _healthy_env()
    environment["PRODUCTION_BACKUP_RESTORE_CERTIFIED_AT"] = (
        NOW + timedelta(minutes=1)
    ).isoformat()

    report = build_launch_report(_healthy_overview(), env=environment, now=NOW)
    check = next(
        item for item in report["checks"]
        if item["code"] == "production_backup_recovery"
    )

    assert report["status"] == "blocked"
    assert check["status"] == "blocked"
    assert "future" in check["detail"].casefold()
    assert environment["PRODUCTION_BACKUP_RESTORE_CERTIFIED_AT"] not in str(report)


def test_restore_certification_before_latest_backup_remains_valid_for_same_pipeline() -> None:
    environment = _healthy_env()
    environment["PRODUCTION_BACKUP_RESTORE_CERTIFIED_AT"] = (
        NOW - timedelta(hours=2)
    ).isoformat()

    report = build_launch_report(_healthy_overview(), env=environment, now=NOW)
    check = next(
        item for item in report["checks"]
        if item["code"] == "production_backup_recovery"
    )

    assert report["status"] == "ready"
    assert check["status"] == "ready"
    assert "unchanged recovery pipeline" in check["detail"].casefold()
    assert environment["PRODUCTION_BACKUP_RESTORE_CERTIFIED_AT"] not in str(report)


def test_newer_daily_backup_does_not_invalidate_unchanged_pipeline_certification() -> None:
    environment = _healthy_env()
    overview = _healthy_overview()

    ready = build_launch_report(overview, env=environment, now=NOW)
    ready_check = next(
        item for item in ready["checks"]
        if item["code"] == "production_backup_recovery"
    )
    assert ready_check["status"] == "ready"

    overview["backup_recovery"].update(
        {
            "latest_event_at": (NOW - timedelta(minutes=10)).isoformat(),
            "age_seconds": 10 * 60,
        }
    )
    still_ready = build_launch_report(overview, env=environment, now=NOW)
    still_ready_check = next(
        item for item in still_ready["checks"]
        if item["code"] == "production_backup_recovery"
    )

    assert still_ready["status"] == "ready"
    assert still_ready_check["status"] == "ready"
    assert "unchanged recovery pipeline" in still_ready_check["detail"].casefold()
    assert overview["backup_recovery"]["latest_event_at"] not in str(still_ready)


def test_expired_restore_certification_blocks_launch() -> None:
    environment = _healthy_env()
    environment["PRODUCTION_BACKUP_RESTORE_CERTIFIED_AT"] = (
        NOW - timedelta(hours=169)
    ).isoformat()

    report = build_launch_report(_healthy_overview(), env=environment, now=NOW)
    check = next(
        item for item in report["checks"]
        if item["code"] == "production_backup_recovery"
    )

    assert report["status"] == "blocked"
    assert check["status"] == "blocked"
    assert "expired" in check["detail"].casefold()
    assert environment["PRODUCTION_BACKUP_RESTORE_CERTIFIED_AT"] not in str(report)


@pytest.mark.parametrize("value", ("", "not-a-number", "23", "721"))
def test_invalid_restore_certification_window_blocks_launch(value: str) -> None:
    environment = _healthy_env()
    environment["PRODUCTION_BACKUP_RESTORE_CERTIFICATION_MAX_AGE_HOURS"] = value

    report = build_launch_report(_healthy_overview(), env=environment, now=NOW)
    check = next(
        item for item in report["checks"]
        if item["code"] == "production_backup_recovery"
    )

    assert report["status"] == "blocked"
    assert check["status"] == "blocked"
    assert "validity window" in check["detail"].casefold()
    if value:
        assert value not in str(report)


def test_recovery_pipeline_drift_blocks_without_exposing_identity(monkeypatch) -> None:
    monkeypatch.setattr(
        launch_readiness,
        "assess_recovery_pipeline",
        lambda: RecoveryPipelineAssessment(
            "blocked", "pipeline_drift", 4
        ),
    )

    report = build_launch_report(_healthy_overview(), env=_healthy_env(), now=NOW)
    check = next(
        item for item in report["checks"]
        if item["code"] == "production_backup_recovery"
    )

    assert report["status"] == "blocked"
    assert check["status"] == "blocked"
    assert "no longer matches" in check["detail"].casefold()
    assert "pipeline_drift" not in str(report)
    assert "54286df" not in str(report)


def test_provider_outage_blocks_launch_and_high_latency_warns() -> None:
    overview = _healthy_overview()
    overview["providers"]["groq"].update({"failure": 60, "success": 40, "circuit": "open"})
    overview["providers"]["whatsapp"]["latency_ms"]["p95"] = 20_000
    report = build_launch_report(overview, env=_healthy_env(), now=NOW)
    statuses = {item["code"]: item["status"] for item in report["checks"]}
    assert statuses["provider_groq"] == "blocked"
    assert statuses["provider_whatsapp"] == "warning"


def test_weak_reminder_or_enabled_support_secrets_block_launch() -> None:
    environment = _healthy_env()
    environment["REMINDER_ENCRYPTION_KEY"] = "weak"
    environment["HUMAN_SUPPORT_ENABLED"] = "true"
    environment["SUPPORT_ENCRYPTION_KEY"] = "weak"
    environment["SUPPORT_API_TOKEN"] = "weak"
    report = build_launch_report(_healthy_overview(), env=environment, now=NOW)
    statuses = {item["code"]: item["status"] for item in report["checks"]}
    assert statuses["reminder_encryption"] == "blocked"
    assert statuses["human_support_security"] == "blocked"
    assert report["status"] == "blocked"


def test_enabled_reminder_worker_without_canary_allowlist_is_blocked() -> None:
    environment = _healthy_env()
    environment.pop("REMINDER_CANARY_SENDERS")

    report = build_launch_report(_healthy_overview(), env=environment, now=NOW)
    checks = {item["code"]: item for item in report["checks"]}

    assert checks["reminder_canary"]["status"] == "blocked"
    assert checks["reminder_delivery"]["status"] == "blocked"
    assert report["status"] == "blocked"


def test_legacy_reminder_compatibility_is_visible_warning() -> None:
    environment = _healthy_env()
    environment["REMINDER_LEGACY_TOKEN_DECRYPTION_ENABLED"] = "true"
    report = build_launch_report(_healthy_overview(), env=environment, now=NOW)
    checks = {item["code"]: item for item in report["checks"]}
    assert checks["reminder_legacy_decryption"]["status"] == "warning"
    assert report["status"] == "warning"


def test_unsafe_brief_scanner_runtime_configuration_blocks_launch_without_leak() -> None:
    environment = _healthy_env()
    sensitive_value = "synthetic-sensitive-invalid-runtime-value"
    environment["BRIEF_SCANNER_RUNTIME_ENABLED"] = sensitive_value

    report = build_launch_report(_healthy_overview(), env=environment, now=NOW)
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

    report = build_launch_report(_healthy_overview(), env=environment, now=NOW)
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

    report = build_launch_report(_healthy_overview(), env=environment, now=NOW)
    checks = {item["code"]: item for item in report["checks"]}

    assert checks["brief_scanner_runtime"]["status"] == "blocked"
    assert "brief_scanner_runtime_draft_unsupported" in (
        checks["brief_scanner_runtime"]["detail"]
    )
    assert report["status"] == "blocked"


def test_report_contains_no_user_content() -> None:
    overview = _healthy_overview()
    environment = _healthy_env()
    report = build_launch_report(overview, env=environment, now=NOW)
    serialized = str(report)
    for forbidden in (
        "49123",
        "وسام",
        "first_name",
        "phone_hash",
        "message text",
        overview["backup_recovery"]["latest_event_at"],
        environment["PRODUCTION_BACKUP_RESTORE_CERTIFIED_AT"],
    ):
        assert forbidden not in serialized


def test_current_checkout_matches_the_restore_certified_pipeline() -> None:
    assessment = certification.assess_recovery_pipeline()

    assert assessment.ready is True
    assert assessment.status == "ready"
    assert assessment.code == "pipeline_certified"
    assert assessment.checked_files == 16


def test_missing_certified_pipeline_file_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        certification,
        "_CERTIFIED_BLOBS",
        {"missing.txt": "0" * 40},
    )

    assessment = certification.assess_recovery_pipeline(tmp_path)

    assert assessment.ready is False
    assert assessment.code == "pipeline_file_missing"
    assert assessment.checked_files == 0
    assert "missing.txt" not in str(assessment)


def test_pipeline_content_drift_fails_closed_without_exposing_identity(
    tmp_path: Path, monkeypatch
) -> None:
    candidate = tmp_path / "pipeline.txt"
    candidate.write_text("certified\n", encoding="utf-8")
    expected = certification._git_blob_sha(candidate)
    monkeypatch.setattr(
        certification,
        "_CERTIFIED_BLOBS",
        {"pipeline.txt": expected},
    )

    assert certification.assess_recovery_pipeline(tmp_path).ready is True

    candidate.write_text("drifted\n", encoding="utf-8")
    assessment = certification.assess_recovery_pipeline(tmp_path)

    assert assessment.ready is False
    assert assessment.code == "pipeline_drift"
    assert expected not in str(assessment)
    assert "pipeline.txt" not in str(assessment)


def test_certification_window_defaults_to_seven_days() -> None:
    assert certification.restore_certification_max_age_hours({}) == 168


def test_certification_window_accepts_only_bounded_explicit_hours() -> None:
    assert certification.restore_certification_max_age_hours(
        {"PRODUCTION_BACKUP_RESTORE_CERTIFICATION_MAX_AGE_HOURS": "24"}
    ) == 24
    assert certification.restore_certification_max_age_hours(
        {"PRODUCTION_BACKUP_RESTORE_CERTIFICATION_MAX_AGE_HOURS": "720"}
    ) == 720

    for value in ("", "not-a-number", "23", "721"):
        assert certification.restore_certification_max_age_hours(
            {"PRODUCTION_BACKUP_RESTORE_CERTIFICATION_MAX_AGE_HOURS": value}
        ) is None
