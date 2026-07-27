"""Privacy-safe configuration readiness for the unwired Brief Scanner runtime.

This validator is intentionally standalone: production readiness and application composition
do not import it yet. It reports only bounded status codes and enabled action names, never
environment values. Unsupported Draft activation and Reminder activation without dedicated
encryption fail closed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from brief_scanner_consent_workflow import BriefScannerConsentAction
from encryption_policy import reminder_encryption_status

_RUNTIME_FLAG = "BRIEF_SCANNER_RUNTIME_ENABLED"
_MISSION_FLAG = "BRIEF_SCANNER_RUNTIME_MISSION_ENABLED"
_DRAFT_FLAG = "BRIEF_SCANNER_RUNTIME_DRAFT_ENABLED"
_REMINDER_FLAG = "BRIEF_SCANNER_RUNTIME_REMINDER_ENABLED"
_FLAGS = (
    _RUNTIME_FLAG,
    _MISSION_FLAG,
    _DRAFT_FLAG,
    _REMINDER_FLAG,
)
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"", "0", "false", "no", "off"})


class BriefScannerRuntimeReadinessStatus(StrEnum):
    DISABLED = "disabled"
    READY = "ready"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class BriefScannerRuntimeReadiness:
    status: BriefScannerRuntimeReadinessStatus
    code: str
    enabled_actions: tuple[BriefScannerConsentAction, ...] = ()

    @property
    def allows_activation(self) -> bool:
        return self.status is BriefScannerRuntimeReadinessStatus.READY

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "code": self.code,
            "enabled_actions": tuple(action.value for action in self.enabled_actions),
            "allows_activation": self.allows_activation,
        }


def _flag(
    environment: Mapping[str, str],
    name: str,
) -> tuple[bool, bool]:
    if name not in environment:
        return False, True
    raw = environment[name]
    if type(raw) is not str:
        return False, False
    value = raw.strip().casefold()
    if value in _TRUE_VALUES:
        return True, True
    if value in _FALSE_VALUES:
        return False, True
    return False, False


def assess_brief_scanner_runtime_readiness(
    environment: Mapping[str, str] | None = None,
) -> BriefScannerRuntimeReadiness:
    """Assess one environment without exposing any configured value."""
    active_environment = os.environ if environment is None else environment
    states = {
        name: _flag(active_environment, name)
        for name in _FLAGS
    }
    if any(not valid for _, valid in states.values()):
        return BriefScannerRuntimeReadiness(
            BriefScannerRuntimeReadinessStatus.BLOCKED,
            "brief_scanner_runtime_flag_invalid",
        )

    runtime_enabled = states[_RUNTIME_FLAG][0]
    mission_enabled = states[_MISSION_FLAG][0]
    draft_enabled = states[_DRAFT_FLAG][0]
    reminder_enabled = states[_REMINDER_FLAG][0]
    action_flags_enabled = mission_enabled or draft_enabled or reminder_enabled

    if not runtime_enabled:
        if action_flags_enabled:
            return BriefScannerRuntimeReadiness(
                BriefScannerRuntimeReadinessStatus.BLOCKED,
                "brief_scanner_runtime_action_without_runtime",
            )
        return BriefScannerRuntimeReadiness(
            BriefScannerRuntimeReadinessStatus.DISABLED,
            "brief_scanner_runtime_disabled",
        )
    if not mission_enabled:
        return BriefScannerRuntimeReadiness(
            BriefScannerRuntimeReadinessStatus.BLOCKED,
            "brief_scanner_runtime_mission_required",
        )
    if draft_enabled:
        return BriefScannerRuntimeReadiness(
            BriefScannerRuntimeReadinessStatus.BLOCKED,
            "brief_scanner_runtime_draft_unsupported",
            enabled_actions=(BriefScannerConsentAction.CREATE_MISSION,),
        )
    if reminder_enabled:
        if reminder_encryption_status(environment=active_environment) != "configured":
            return BriefScannerRuntimeReadiness(
                BriefScannerRuntimeReadinessStatus.BLOCKED,
                "brief_scanner_runtime_reminder_encryption_not_ready",
                enabled_actions=(BriefScannerConsentAction.CREATE_MISSION,),
            )
        return BriefScannerRuntimeReadiness(
            BriefScannerRuntimeReadinessStatus.READY,
            "brief_scanner_runtime_mission_reminder_ready",
            enabled_actions=(
                BriefScannerConsentAction.CREATE_MISSION,
                BriefScannerConsentAction.CREATE_REMINDER,
            ),
        )
    return BriefScannerRuntimeReadiness(
        BriefScannerRuntimeReadinessStatus.READY,
        "brief_scanner_runtime_mission_ready",
        enabled_actions=(BriefScannerConsentAction.CREATE_MISSION,),
    )
