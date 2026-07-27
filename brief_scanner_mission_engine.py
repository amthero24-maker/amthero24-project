"""Consent-bound, side-effect-free execution preparation for Brief Scanner Missions.

This boundary converts an immutable planning bundle and its matching consent artifacts into typed
commands for a later executor. It never persists a Mission, calls a model, generates a draft,
schedules a reminder, sends a message, or emits telemetry.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from brief_scanner_consent_workflow import (
    BriefScannerConsentAction,
    BriefScannerConsentPlan,
    BriefScannerConsentReceipt,
    brief_scanner_planning_fingerprint,
)
from brief_scanner_draft_planner import BriefScannerDraftPlan
from brief_scanner_mission_planner import (
    BriefScannerMissionKind,
    BriefScannerMissionPlan,
    BriefScannerMissionPlanningBundle,
)
from brief_scanner_reminder_planner import BriefScannerReminderPlan


class BriefScannerMissionCommandKind(StrEnum):
    CREATE_MISSION = "create_mission"
    GENERATE_DRAFT = "generate_draft"
    CREATE_REMINDER = "create_reminder"


@dataclass(frozen=True)
class BriefScannerMissionCommand:
    kind: BriefScannerMissionCommandKind
    mission: BriefScannerMissionPlan
    draft: BriefScannerDraftPlan | None = None
    reminder: BriefScannerReminderPlan | None = None
    reminder_delivery_time: str = ""
    reminder_timezone_name: str = ""
    requires_executor: bool = True
    allows_persistence: bool = False
    allows_generation: bool = False
    allows_scheduling: bool = False
    allows_side_effects: bool = False


@dataclass(frozen=True)
class BriefScannerMissionExecutionPlan:
    commands: tuple[BriefScannerMissionCommand, ...]
    declined_actions: tuple[BriefScannerConsentAction, ...]
    consent_verified: bool = True
    requires_executor: bool = True
    allows_persistence: bool = False
    allows_generation: bool = False
    allows_scheduling: bool = False
    allows_side_effects: bool = False


_COMMAND_KIND = {
    BriefScannerConsentAction.CREATE_MISSION: BriefScannerMissionCommandKind.CREATE_MISSION,
    BriefScannerConsentAction.GENERATE_DRAFT: BriefScannerMissionCommandKind.GENERATE_DRAFT,
    BriefScannerConsentAction.CREATE_REMINDER: BriefScannerMissionCommandKind.CREATE_REMINDER,
}


def _expected_actions(
    bundle: BriefScannerMissionPlanningBundle,
) -> tuple[BriefScannerConsentAction, ...]:
    actions = [BriefScannerConsentAction.CREATE_MISSION]
    if bundle.draft is not None:
        actions.append(BriefScannerConsentAction.GENERATE_DRAFT)
    if bundle.reminder is not None:
        actions.append(BriefScannerConsentAction.CREATE_REMINDER)
    return tuple(actions)


def _require_safe_bundle(bundle: BriefScannerMissionPlanningBundle) -> None:
    unsafe = (
        type(bundle) is not BriefScannerMissionPlanningBundle
        or not bundle.requires_confirmation
        or bundle.allows_generation
        or bundle.allows_persistence
        or bundle.allows_scheduling
        or bundle.allows_side_effects
        or not bundle.mission.requires_confirmation
        or bundle.mission.allows_side_effects
        or bundle.mission.kind == BriefScannerMissionKind.REVIEW_ONLY
    )
    if bundle.draft is not None:
        unsafe = unsafe or (
            not bundle.draft.requires_confirmation
            or bundle.draft.allows_generation
            or bundle.draft.allows_side_effects
            or not bundle.draft.ready_for_confirmation
        )
    if bundle.reminder is not None:
        unsafe = unsafe or (
            not bundle.reminder.requires_confirmation
            or not bundle.reminder.requires_memory_consent
            or not bundle.reminder.requires_delivery_time
            or bundle.reminder.allows_persistence
            or bundle.reminder.allows_scheduling
            or bundle.reminder.allows_side_effects
        )
    if unsafe:
        raise ValueError("brief_scanner_engine_bundle_invalid")


def _require_matching_consent(
    bundle: BriefScannerMissionPlanningBundle,
    consent_plan: BriefScannerConsentPlan,
    receipt: BriefScannerConsentReceipt,
) -> None:
    expected = _expected_actions(bundle)
    expected_fingerprint = brief_scanner_planning_fingerprint(bundle)
    approved = frozenset(receipt.approved_actions)
    declined = frozenset(receipt.declined_actions)
    if (
        type(consent_plan) is not BriefScannerConsentPlan
        or type(receipt) is not BriefScannerConsentReceipt
        or not consent_plan.ready_for_decision
        or not consent_plan.requires_explicit_decision
        or consent_plan.allows_execution
        or consent_plan.allows_side_effects
        or not receipt.complete
        or receipt.allows_execution
        or receipt.allows_side_effects
        or not receipt.memory_consent_active
        or not consent_plan.memory_consent_active
        or consent_plan.planning_fingerprint != expected_fingerprint
        or receipt.planning_fingerprint != expected_fingerprint
        or receipt.planning_fingerprint != consent_plan.planning_fingerprint
        or consent_plan.requested_actions != expected
        or receipt.requested_actions != expected
        or receipt.requested_actions != consent_plan.requested_actions
        or approved.intersection(declined)
        or approved.union(declined) != frozenset(expected)
        or len(receipt.approved_actions) != len(approved)
        or len(receipt.declined_actions) != len(declined)
        or tuple(action for action in expected if action in approved)
        != receipt.approved_actions
        or tuple(action for action in expected if action in declined)
        != receipt.declined_actions
    ):
        raise ValueError("brief_scanner_engine_consent_invalid")

    if BriefScannerConsentAction.CREATE_MISSION not in receipt.approved_actions:
        if receipt.approved_actions:
            raise ValueError("brief_scanner_engine_consent_dependency_invalid")
        return

    if bundle.reminder is not None and (
        consent_plan.reminder_delivery_time is None
        or not consent_plan.reminder_timezone_name
    ):
        raise ValueError("brief_scanner_engine_reminder_context_invalid")


def prepare_brief_scanner_mission_execution(
    bundle: BriefScannerMissionPlanningBundle,
    consent_plan: BriefScannerConsentPlan,
    receipt: BriefScannerConsentReceipt,
) -> BriefScannerMissionExecutionPlan:
    """Prepare ordered commands only for explicitly approved actions.

    The result remains inert. A separate, future executor must revalidate authorization and own
    each persistence, generation, and scheduling side effect.
    """
    _require_safe_bundle(bundle)
    _require_matching_consent(bundle, consent_plan, receipt)

    commands: list[BriefScannerMissionCommand] = []
    for action in _expected_actions(bundle):
        if action not in receipt.approved_actions:
            continue
        commands.append(
            BriefScannerMissionCommand(
                kind=_COMMAND_KIND[action],
                mission=bundle.mission,
                draft=bundle.draft
                if action == BriefScannerConsentAction.GENERATE_DRAFT
                else None,
                reminder=bundle.reminder
                if action == BriefScannerConsentAction.CREATE_REMINDER
                else None,
                reminder_delivery_time=(
                    consent_plan.reminder_delivery_time.isoformat(timespec="minutes")
                    if action == BriefScannerConsentAction.CREATE_REMINDER
                    and consent_plan.reminder_delivery_time is not None
                    else ""
                ),
                reminder_timezone_name=(
                    consent_plan.reminder_timezone_name
                    if action == BriefScannerConsentAction.CREATE_REMINDER
                    else ""
                ),
            )
        )

    return BriefScannerMissionExecutionPlan(
        commands=tuple(commands),
        declined_actions=receipt.declined_actions,
    )
