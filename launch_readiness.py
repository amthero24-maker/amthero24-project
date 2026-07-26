"""Privacy-safe Beta launch gates derived from configuration and aggregate health."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping


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


def build_launch_report(
    overview: dict[str, Any],
    *,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return actionable Beta gates using aggregate metrics only."""
    environment = env or os.environ
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

    app_secret = bool(str(environment.get("META_APP_SECRET", "")).strip())
    signature_required = _flag(environment, "WEBHOOK_SIGNATURE_REQUIRED", False)
    if app_secret and signature_required:
        checks.append(LaunchCheck("meta_signature", "ready", "Meta webhook signatures are enforced."))
    elif app_secret:
        checks.append(LaunchCheck("meta_signature", "warning", "Meta App Secret exists, but fail-closed signature mode is not enabled.", "Set WEBHOOK_SIGNATURE_REQUIRED=true after confirming production webhook delivery."))
    else:
        checks.append(LaunchCheck("meta_signature", "blocked", "Meta webhook authenticity is not configured.", "Add META_APP_SECRET and set WEBHOOK_SIGNATURE_REQUIRED=true."))

    checks.append(
        LaunchCheck("admin_access", "ready", "Protected admin access is configured.")
        if str(environment.get("ADMIN_API_TOKEN", "")).strip()
        else LaunchCheck("admin_access", "blocked", "Protected admin access is disabled.", "Add a strong ADMIN_API_TOKEN in Railway.")
    )

    privacy_enabled = _flag(environment, "PRIVACY_RETENTION_ENABLED", True)
    checks.append(
        LaunchCheck("privacy_retention", "ready", "Automatic privacy retention is enabled.")
        if privacy_enabled
        else LaunchCheck("privacy_retention", "blocked", "Automatic privacy retention is disabled.", "Set PRIVACY_RETENTION_ENABLED=true before Beta.")
    )

    reminder_enabled = _flag(environment, "REMINDER_WORKER_ENABLED", True)
    reminder_template = bool(str(environment.get("WHATSAPP_REMINDER_TEMPLATE", "")).strip())
    if reminder_enabled and reminder_template:
        checks.append(LaunchCheck("reminder_delivery", "ready", "Reminder worker and approved template are configured."))
    elif reminder_enabled:
        checks.append(LaunchCheck("reminder_delivery", "warning", "Reminders work only inside the 24-hour service window.", "Approve and configure WHATSAPP_REMINDER_TEMPLATE before testing long-term reminders."))
    else:
        checks.append(LaunchCheck("reminder_delivery", "warning", "Reminder worker is disabled.", "Enable it when reminder delivery is ready for Beta testing."))

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
