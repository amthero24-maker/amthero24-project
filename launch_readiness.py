"""Privacy-safe Beta launch gates derived from configuration and aggregate health."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

from brief_scanner_runtime_readiness import (
    BriefScannerRuntimeReadinessStatus,
    assess_brief_scanner_runtime_readiness,
)
from encryption_policy import (
    admin_api_token_status,
    legacy_reminder_decryption_enabled,
    reminder_encryption_status,
    support_api_token_status,
    support_encryption_status,
)


@dataclass(frozen=True)
class LaunchCheck:
    code: str
    status: str
    detail: str
    action: str = ""

    def as_dict(self) -> dict[str, str]:
        payload = {"code": self.code, "status": self.status, "detail": self.detail}
        if self.action:
            payload["action"] = self.action
        return payload


def _flag(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return str(env.get(name, fallback)).strip().casefold() in {"1", "true", "yes", "on"}


def _utc_timestamp(value: Any) -> datetime | None:
    """Parse one explicit timezone-aware timestamp without reflecting its value."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _count(mapping: Any, key: str) -> int:
    if not isinstance(mapping, dict):
        return 0
    try:
        return int(mapping.get(key, 0))
    except (TypeError, ValueError):
        return 0


def _rate(failed: int, total: int) -> float:
    return round((failed / total) * 100, 1) if total > 0 else 0.0


def _provider_check(name: str, payload: dict[str, Any]) -> LaunchCheck:
    total = int(payload.get("total", 0) or 0)
    failed = int(payload.get("failure", 0) or 0)
    rejected = int(payload.get("circuit_rejected", 0) or 0)
    rate = _rate(failed, total)
    circuit = str(payload.get("circuit") or "closed")
    latency = payload.get("latency_ms", {}) if isinstance(payload.get("latency_ms"), dict) else {}
    p95 = int(latency.get("p95", 0) or 0)

    if circuit == "open":
        return LaunchCheck(
            f"provider_{name}", "blocked", f"{name} circuit is open.",
            "Wait for provider recovery and verify the next successful probe.",
        )
    if total >= 5 and rate >= 50:
        return LaunchCheck(
            f"provider_{name}", "blocked", f"{name} failure rate is {rate}% over the last 24 hours.",
            "Investigate provider credentials, availability, and recent deployment changes.",
        )
    if total >= 5 and rate >= 20:
        return LaunchCheck(
            f"provider_{name}", "warning", f"{name} failure rate is {rate}% over the last 24 hours.",
            "Run a controlled smoke test before inviting more Beta users.",
        )
    if p95 >= 15_000:
        return LaunchCheck(
            f"provider_{name}", "warning", f"{name} p95 latency is {p95} ms.",
            "Check timeouts and keep the Beta group small until latency stabilizes.",
        )
    detail = "No recent traffic yet." if total == 0 else f"{name} is healthy across {total} recent operations."
    if rejected:
        detail += f" Circuit rejected {rejected} operations."
    return LaunchCheck(f"provider_{name}", "ready", detail)


def _brief_scanner_runtime_check(environment: Mapping[str, str]) -> LaunchCheck:
    readiness = assess_brief_scanner_runtime_readiness(environment)
    if readiness.status is BriefScannerRuntimeReadinessStatus.BLOCKED:
        return LaunchCheck(
            "brief_scanner_runtime",
            "blocked",
            f"Brief Scanner runtime activation is blocked by {readiness.code}.",
            "Keep the runtime disabled and correct the named configuration gate before activation.",
        )
    if readiness.status is BriefScannerRuntimeReadinessStatus.DISABLED:
        return LaunchCheck(
            "brief_scanner_runtime",
            "ready",
            "Brief Scanner runtime is safely disabled.",
        )
    actions = ", ".join(action.value for action in readiness.enabled_actions)
    return LaunchCheck(
        "brief_scanner_runtime",
        "ready",
        f"Brief Scanner runtime configuration is ready for {actions}.",
    )


def _backup_recovery_check(
    overview: dict[str, Any],
    environment: Mapping[str, str],
    current: datetime,
) -> LaunchCheck:
    payload = overview.get("backup_recovery")
    metrics = payload if isinstance(payload, dict) else {}
    receipt = str(metrics.get("receipt") or "missing")
    safe_receipts = {
        "missing",
        "recent_success",
        "latest_started",
        "latest_failure",
        "latest_unknown",
        "stale_success",
        "stale_started",
        "stale_failure",
        "stale_unknown",
        "invalid_timestamp",
        "future_timestamp",
    }
    safe_receipt = receipt if receipt in safe_receipts else "invalid"
    restore_certified = _flag(
        environment,
        "PRODUCTION_BACKUP_RESTORE_CERTIFIED",
        False,
    )
    restore_certified_at = _utc_timestamp(
        environment.get("PRODUCTION_BACKUP_RESTORE_CERTIFIED_AT", "")
    )

    if safe_receipt != "recent_success":
        return LaunchCheck(
            "production_backup_recovery",
            "blocked",
            f"Persistent encrypted production backup receipt is {safe_receipt}.",
            "Attach the dedicated Railway backup volume, complete a successful encrypted backup, prove persistence across restart, and restore the actual artifact into an isolated PostgreSQL target.",
        )
    try:
        backup_age_seconds = int(metrics.get("age_seconds", -1))
    except (TypeError, ValueError):
        backup_age_seconds = -1
    if backup_age_seconds < 0:
        return LaunchCheck(
            "production_backup_recovery",
            "blocked",
            "The latest successful backup receipt has an invalid age.",
            "Repeat the persistent encrypted backup and verify the privacy-safe receipt before restore certification.",
        )
    if not restore_certified:
        return LaunchCheck(
            "production_backup_recovery",
            "blocked",
            "A recent persistent encrypted backup exists, but isolated restore certification is not approved.",
            "Complete the owner-authorized isolated restore of the actual persistent artifact, verify current schema and privacy-safe parity, then set PRODUCTION_BACKUP_RESTORE_CERTIFIED=true and record its UTC certification time.",
        )
    if restore_certified_at is None:
        return LaunchCheck(
            "production_backup_recovery",
            "blocked",
            "Restore approval exists, but its UTC certification time is missing or invalid.",
            "After restoring the actual latest persistent artifact, set PRODUCTION_BACKUP_RESTORE_CERTIFIED_AT to the timezone-aware ISO 8601 completion time.",
        )
    if restore_certified_at > current:
        return LaunchCheck(
            "production_backup_recovery",
            "blocked",
            "The isolated restore certification time is in the future.",
            "Correct the certification time only after the owner-authorized isolated restore has completed.",
        )

    latest_backup_at = current - timedelta(seconds=backup_age_seconds)
    if restore_certified_at < latest_backup_at:
        return LaunchCheck(
            "production_backup_recovery",
            "blocked",
            "The isolated restore certification predates the latest successful production backup.",
            "Restore the actual latest persistent artifact, verify current schema and privacy-safe parity, then update the restore certification and UTC completion time.",
        )
    return LaunchCheck(
        "production_backup_recovery",
        "ready",
        "The latest persistent encrypted production backup has a time-bound owner-approved isolated restore certification.",
    )


def build_launch_report(
    overview: dict[str, Any],
    *,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return actionable Beta gates using aggregate metrics only."""
    environment = os.environ if env is None else env
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    else:
        current = current.astimezone(UTC)

    checks: list[LaunchCheck] = []
    backend = str(overview.get("storage_backend") or "unknown")
    checks.append(
        LaunchCheck("postgresql", "ready", "PostgreSQL is active.")
        if backend == "postgresql"
        else LaunchCheck("postgresql", "blocked", f"Storage backend is {backend}.", "Restore the Railway PostgreSQL connection before Beta traffic.")
    )

    fallback_allowed = _flag(environment, "DATABASE_FALLBACK_ALLOWED", False)
    checks.append(
        LaunchCheck("database_fail_closed", "ready", "PostgreSQL failures stop traffic instead of creating divergent JSON state.")
        if not fallback_allowed
        else LaunchCheck(
            "database_fail_closed",
            "blocked",
            "Emergency JSON fallback is allowed while PostgreSQL is configured.",
            "Set DATABASE_FALLBACK_ALLOWED=false before Beta to prevent split-brain user memory.",
        )
    )
    checks.append(_backup_recovery_check(overview, environment, current))

    app_secret = bool(str(environment.get("META_APP_SECRET", "")).strip())
    signature_required = _flag(environment, "WEBHOOK_SIGNATURE_REQUIRED", False)
    if app_secret and signature_required:
        checks.append(LaunchCheck("meta_signature", "ready", "Meta webhook signatures are enforced."))
    elif app_secret:
        checks.append(LaunchCheck("meta_signature", "warning", "Meta App Secret exists, but fail-closed signature mode is not enabled.", "Set WEBHOOK_SIGNATURE_REQUIRED=true after confirming production webhook delivery."))
    else:
        checks.append(LaunchCheck("meta_signature", "blocked", "Meta webhook authenticity is not configured.", "Add META_APP_SECRET and set WEBHOOK_SIGNATURE_REQUIRED=true."))

    admin_status = admin_api_token_status(environment=environment)
    checks.append(
        LaunchCheck("admin_access", "ready", "Protected admin access uses a strong dedicated token.")
        if admin_status == "configured"
        else LaunchCheck(
            "admin_access",
            "blocked",
            f"Protected admin access token is {admin_status}.",
            "Set ADMIN_API_TOKEN to a unique random value of at least 32 characters.",
        )
    )

    privacy_enabled = _flag(environment, "PRIVACY_RETENTION_ENABLED", True)
    checks.append(
        LaunchCheck("privacy_retention", "ready", "Automatic privacy retention is enabled.")
        if privacy_enabled
        else LaunchCheck("privacy_retention", "blocked", "Automatic privacy retention is disabled.", "Set PRIVACY_RETENTION_ENABLED=true before Beta.")
    )

    reminder_enabled = _flag(environment, "REMINDER_WORKER_ENABLED", False)
    reminder_key_status = reminder_encryption_status(environment=environment)
    reminder_template = bool(str(environment.get("WHATSAPP_REMINDER_TEMPLATE", "")).strip())
    reminder_canary = bool(str(environment.get("REMINDER_CANARY_SENDERS", "")).strip())
    if reminder_enabled and reminder_key_status != "configured":
        checks.append(LaunchCheck(
            "reminder_encryption",
            "blocked",
            f"Reminder encryption key is {reminder_key_status}; new reminders and delivery are disabled.",
            "Set REMINDER_ENCRYPTION_KEY to a unique random value of at least 32 characters.",
        ))
    elif reminder_enabled:
        checks.append(LaunchCheck("reminder_encryption", "ready", "Reminder recipients use a dedicated strong encryption key."))
    else:
        checks.append(LaunchCheck("reminder_encryption", "warning", "Reminder worker is disabled.", "Enable it after reminder encryption and delivery are configured."))

    if reminder_enabled and reminder_canary:
        checks.append(LaunchCheck("reminder_canary", "ready", "Reminder delivery is restricted to an explicit Canary allowlist."))
    elif reminder_enabled:
        checks.append(LaunchCheck(
            "reminder_canary",
            "blocked",
            "Reminder worker is enabled without a Canary allowlist.",
            "Set REMINDER_CANARY_SENDERS to the one exact controlled test number before starting delivery.",
        ))
    else:
        checks.append(LaunchCheck("reminder_canary", "ready", "Reminder worker is disabled; no recipient can be claimed."))

    if reminder_enabled and reminder_key_status == "configured" and reminder_canary and reminder_template:
        checks.append(LaunchCheck("reminder_delivery", "ready", "Reminder worker and approved template are configured."))
    elif reminder_enabled and reminder_key_status == "configured" and reminder_canary:
        checks.append(LaunchCheck("reminder_delivery", "warning", "Reminders work only inside the 24-hour service window.", "Approve and configure WHATSAPP_REMINDER_TEMPLATE before testing long-term reminders."))
    elif reminder_enabled and reminder_key_status == "configured":
        checks.append(LaunchCheck(
            "reminder_delivery",
            "blocked",
            "Reminder delivery is blocked until the Canary allowlist is configured.",
            "Set REMINDER_CANARY_SENDERS to the one exact controlled test number.",
        ))
    elif not reminder_enabled:
        checks.append(LaunchCheck("reminder_delivery", "warning", "Reminder worker is disabled.", "Enable it when reminder delivery is ready for Beta testing."))

    if legacy_reminder_decryption_enabled(environment=environment):
        checks.append(LaunchCheck(
            "reminder_legacy_decryption",
            "warning",
            "Temporary WhatsApp-token decryption compatibility is enabled for historical reminders.",
            "Migrate active reminder ciphertext to the dedicated key, then set REMINDER_LEGACY_TOKEN_DECRYPTION_ENABLED=false.",
        ))
    else:
        checks.append(LaunchCheck("reminder_legacy_decryption", "ready", "Legacy WhatsApp-token decryption is disabled."))

    support_enabled = _flag(environment, "HUMAN_SUPPORT_ENABLED", False)
    if support_enabled:
        support_key = support_encryption_status(environment=environment)
        support_token = support_api_token_status(environment=environment)
        if support_key == "configured" and support_token == "configured":
            checks.append(LaunchCheck("human_support_security", "ready", "Human support uses dedicated strong encryption and API tokens."))
        else:
            checks.append(LaunchCheck(
                "human_support_security",
                "blocked",
                f"Human support encryption is {support_key} and operator token is {support_token}.",
                "Use unique random values of at least 32 characters for SUPPORT_ENCRYPTION_KEY and SUPPORT_API_TOKEN.",
            ))
    else:
        checks.append(LaunchCheck("human_support_security", "ready", "Human support is disabled until an operator team is configured."))

    checks.append(_brief_scanner_runtime_check(environment))

    messages = overview.get("messages_24h", {}) if isinstance(overview.get("messages_24h"), dict) else {}
    message_total = int(messages.get("total", 0) or 0)
    message_failed = int(messages.get("failed", 0) or 0)
    message_rate = _rate(message_failed, message_total)
    if message_total >= 10 and message_rate >= 20:
        checks.append(LaunchCheck("message_failures", "blocked", f"Inbound processing failure rate is {message_rate}%.", "Inspect recent deployment and provider failures before onboarding users."))
    elif message_total >= 10 and message_rate >= 5:
        checks.append(LaunchCheck("message_failures", "warning", f"Inbound processing failure rate is {message_rate}%.", "Review failed webhook records during the Beta smoke test."))
    else:
        checks.append(LaunchCheck("message_failures", "ready", "Inbound processing failure rate is within the Beta threshold."))

    providers = overview.get("providers", {}) if isinstance(overview.get("providers"), dict) else {}
    checks.append(_provider_check("groq", providers.get("groq", {}) if isinstance(providers.get("groq"), dict) else {}))
    checks.append(_provider_check("whatsapp", providers.get("whatsapp", {}) if isinstance(providers.get("whatsapp"), dict) else {}))

    reminders = overview.get("reminders", {}) if isinstance(overview.get("reminders"), dict) else {}
    reminder_status = reminders.get("by_status", {}) if isinstance(reminders.get("by_status"), dict) else {}
    blocked_templates = _count(reminder_status, "blocked_template")
    failed_reminders = _count(reminder_status, "failed")
    if blocked_templates > 0:
        checks.append(LaunchCheck("reminder_backlog", "warning", f"{blocked_templates} reminder(s) were blocked because no template was available.", "Configure the approved utility template and retest."))
    elif failed_reminders > 0:
        checks.append(LaunchCheck("reminder_backlog", "warning", f"{failed_reminders} reminder(s) are in failed state.", "Inspect aggregate provider health and retry behavior."))
    else:
        checks.append(LaunchCheck("reminder_backlog", "ready", "No failed or template-blocked reminders are recorded."))

    abuse = overview.get("abuse_guard", {}) if isinstance(overview.get("abuse_guard"), dict) else {}
    active_blocks = int(abuse.get("active_blocks", 0) or 0)
    checks.append(
        LaunchCheck("abuse_guard", "warning", f"{active_blocks} sender block(s) are currently active.", "Confirm thresholds are not blocking normal Beta behavior.")
        if active_blocks > 5
        else LaunchCheck("abuse_guard", "ready", "Abuse protection is within the expected range.")
    )

    entitlements = overview.get("entitlements", {}) if isinstance(overview.get("entitlements"), dict) else {}
    entitlement_mode = str(entitlements.get("mode") or "observe-only")
    checks.append(
        LaunchCheck("entitlements", "ready", "Entitlements are in observe-only mode; current users will not be paywalled.")
        if entitlement_mode == "observe-only"
        else LaunchCheck("entitlements", "warning", "Entitlement enforcement is active.", "Verify plan assignments and limits before inviting Beta users.")
    )

    statuses = [check.status for check in checks]
    overall = "blocked" if "blocked" in statuses else ("warning" if "warning" in statuses else "ready")
    counts = {status: statuses.count(status) for status in ("ready", "warning", "blocked")}
    next_actions = [check.action for check in checks if check.action]
    return {
        "generated_at": current.isoformat(),
        "status": overall,
        "summary": counts,
        "checks": [check.as_dict() for check in checks],
        "next_actions": next_actions,
        "launch_scope": "controlled_beta",
    }
