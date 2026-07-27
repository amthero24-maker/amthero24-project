from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from brief_scanner_consent_workflow import BriefScannerConsentAction
from brief_scanner_runtime_readiness import (
    BriefScannerRuntimeReadinessStatus,
    assess_brief_scanner_runtime_readiness,
)

_STRONG_REMINDER_KEY = "runtime-reminder-key-2026-unique-7fA9xQ2mLp8V"


def test_missing_flags_are_safely_disabled() -> None:
    readiness = assess_brief_scanner_runtime_readiness({})

    assert readiness.status is BriefScannerRuntimeReadinessStatus.DISABLED
    assert readiness.code == "brief_scanner_runtime_disabled"
    assert readiness.enabled_actions == ()
    assert readiness.allows_activation is False


def test_action_flag_without_global_runtime_is_blocked() -> None:
    readiness = assess_brief_scanner_runtime_readiness(
        {"BRIEF_SCANNER_RUNTIME_MISSION_ENABLED": "true"}
    )

    assert readiness.status is BriefScannerRuntimeReadinessStatus.BLOCKED
    assert readiness.code == "brief_scanner_runtime_action_without_runtime"


def test_runtime_requires_mission_and_rejects_unsupported_draft() -> None:
    mission_missing = assess_brief_scanner_runtime_readiness(
        {"BRIEF_SCANNER_RUNTIME_ENABLED": "true"}
    )
    draft_enabled = assess_brief_scanner_runtime_readiness(
        {
            "BRIEF_SCANNER_RUNTIME_ENABLED": "true",
            "BRIEF_SCANNER_RUNTIME_MISSION_ENABLED": "true",
            "BRIEF_SCANNER_RUNTIME_DRAFT_ENABLED": "true",
        }
    )

    assert mission_missing.code == "brief_scanner_runtime_mission_required"
    assert draft_enabled.code == "brief_scanner_runtime_draft_unsupported"
    assert draft_enabled.enabled_actions == (
        BriefScannerConsentAction.CREATE_MISSION,
    )
    assert draft_enabled.allows_activation is False


@pytest.mark.parametrize("reminder_key", ["", "weak"])
def test_reminder_requires_dedicated_strong_encryption(reminder_key: str) -> None:
    readiness = assess_brief_scanner_runtime_readiness(
        {
            "BRIEF_SCANNER_RUNTIME_ENABLED": "true",
            "BRIEF_SCANNER_RUNTIME_MISSION_ENABLED": "true",
            "BRIEF_SCANNER_RUNTIME_REMINDER_ENABLED": "true",
            "REMINDER_ENCRYPTION_KEY": reminder_key,
        }
    )

    assert readiness.status is BriefScannerRuntimeReadinessStatus.BLOCKED
    assert readiness.code == "brief_scanner_runtime_reminder_encryption_not_ready"
    assert readiness.enabled_actions == (
        BriefScannerConsentAction.CREATE_MISSION,
    )


def test_supported_mission_routes_are_reported_ready() -> None:
    mission = assess_brief_scanner_runtime_readiness(
        {
            "BRIEF_SCANNER_RUNTIME_ENABLED": "true",
            "BRIEF_SCANNER_RUNTIME_MISSION_ENABLED": "true",
        }
    )
    mission_reminder = assess_brief_scanner_runtime_readiness(
        {
            "BRIEF_SCANNER_RUNTIME_ENABLED": "true",
            "BRIEF_SCANNER_RUNTIME_MISSION_ENABLED": "true",
            "BRIEF_SCANNER_RUNTIME_REMINDER_ENABLED": "true",
            "REMINDER_ENCRYPTION_KEY": _STRONG_REMINDER_KEY,
        }
    )

    assert mission.status is BriefScannerRuntimeReadinessStatus.READY
    assert mission.code == "brief_scanner_runtime_mission_ready"
    assert mission.enabled_actions == (BriefScannerConsentAction.CREATE_MISSION,)
    assert mission_reminder.status is BriefScannerRuntimeReadinessStatus.READY
    assert mission_reminder.code == "brief_scanner_runtime_mission_reminder_ready"
    assert mission_reminder.enabled_actions == (
        BriefScannerConsentAction.CREATE_MISSION,
        BriefScannerConsentAction.CREATE_REMINDER,
    )


@pytest.mark.parametrize(
    "value",
    ["sensitive-invalid-flag-value", "2", "maybe"],
)
def test_invalid_flag_values_fail_closed_without_echoing_values(value: str) -> None:
    readiness = assess_brief_scanner_runtime_readiness(
        {"BRIEF_SCANNER_RUNTIME_ENABLED": value}
    )
    serialized = str(readiness.as_dict())

    assert readiness.status is BriefScannerRuntimeReadinessStatus.BLOCKED
    assert readiness.code == "brief_scanner_runtime_flag_invalid"
    assert value not in serialized


def test_readiness_is_immutable_and_serializes_only_bounded_fields() -> None:
    readiness = assess_brief_scanner_runtime_readiness(
        {
            "BRIEF_SCANNER_RUNTIME_ENABLED": "true",
            "BRIEF_SCANNER_RUNTIME_MISSION_ENABLED": "true",
        }
    )

    assert readiness.as_dict() == {
        "status": "ready",
        "code": "brief_scanner_runtime_mission_ready",
        "enabled_actions": ("create_mission",),
        "allows_activation": True,
    }
    with pytest.raises(FrozenInstanceError):
        readiness.code = "forged"  # type: ignore[misc]
