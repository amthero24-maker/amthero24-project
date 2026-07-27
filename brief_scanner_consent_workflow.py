"""Pure consent planning and decision recording for Brief Scanner Mission plans.

This module never persists consent, executes a Mission, generates a draft, schedules a reminder,
or emits telemetry. It only validates that required context exists and records explicit typed
decisions for a later execution boundary.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import time
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from brief_scanner_draft_planner import BriefScannerDraftInput
from brief_scanner_mission_planner import (
    BriefScannerMissionKind,
    BriefScannerMissionPlanningBundle,
)


class BriefScannerConsentAction(StrEnum):
    CREATE_MISSION = "create_mission"
    GENERATE_DRAFT = "generate_draft"
    CREATE_REMINDER = "create_reminder"


class BriefScannerConsentInput(StrEnum):
    MEMORY_CONSENT = "memory_consent"
    DRAFT_RECIPIENT_ORGANIZATION = "draft_recipient_organization"
    DRAFT_RESPONSE_INSTRUCTION = "draft_response_instruction"
    REMINDER_DELIVERY_TIME = "reminder_delivery_time"
    REMINDER_TIMEZONE = "reminder_timezone"


class BriefScannerConsentDecision(StrEnum):
    APPROVE = "approve"
    DECLINE = "decline"


@dataclass(frozen=True)
class BriefScannerConsentChoice:
    action: BriefScannerConsentAction
    decision: BriefScannerConsentDecision


@dataclass(frozen=True)
class BriefScannerConsentPlan:
    requested_actions: tuple[BriefScannerConsentAction, ...]
    missing_inputs: tuple[BriefScannerConsentInput, ...]
    memory_consent_active: bool
    planning_fingerprint: str
    reminder_delivery_time: time | None = None
    reminder_timezone_name: str = ""
    requires_explicit_decision: bool = True
    allows_execution: bool = False
    allows_side_effects: bool = False

    @property
    def ready_for_decision(self) -> bool:
        return not self.missing_inputs


@dataclass(frozen=True)
class BriefScannerConsentReceipt:
    requested_actions: tuple[BriefScannerConsentAction, ...]
    approved_actions: tuple[BriefScannerConsentAction, ...]
    declined_actions: tuple[BriefScannerConsentAction, ...]
    memory_consent_active: bool
    planning_fingerprint: str = ""
    complete: bool = True
    allows_execution: bool = False
    allows_side_effects: bool = False

    @property
    def all_requested_actions_approved(self) -> bool:
        return self.approved_actions == self.requested_actions


_DRAFT_INPUT_MAP = {
    BriefScannerDraftInput.RECIPIENT_ORGANIZATION: (
        BriefScannerConsentInput.DRAFT_RECIPIENT_ORGANIZATION
    ),
    BriefScannerDraftInput.RESPONSE_INSTRUCTION: (
        BriefScannerConsentInput.DRAFT_RESPONSE_INSTRUCTION
    ),
}


def brief_scanner_planning_fingerprint(
    bundle: BriefScannerMissionPlanningBundle,
) -> str:
    """Return a version-local digest binding consent to the exact immutable plan."""
    if type(bundle) is not BriefScannerMissionPlanningBundle:
        raise ValueError("brief_scanner_consent_bundle_type_invalid")
    return hashlib.sha256(repr(bundle).encode("utf-8")).hexdigest()


def _require_read_only_bundle(bundle: BriefScannerMissionPlanningBundle) -> None:
    unsafe = (
        not bundle.requires_confirmation
        or bundle.allows_generation
        or bundle.allows_persistence
        or bundle.allows_scheduling
        or bundle.allows_side_effects
        or not bundle.mission.requires_confirmation
        or bundle.mission.allows_side_effects
    )
    if bundle.draft is not None:
        unsafe = unsafe or (
            not bundle.draft.requires_confirmation
            or bundle.draft.allows_generation
            or bundle.draft.allows_side_effects
        )
    if bundle.reminder is not None:
        unsafe = unsafe or (
            not bundle.reminder.requires_confirmation
            or not bundle.reminder.requires_memory_consent
            or bundle.reminder.allows_persistence
            or bundle.reminder.allows_scheduling
            or bundle.reminder.allows_side_effects
        )
    if unsafe:
        raise ValueError("brief_scanner_consent_bundle_not_read_only")


def _validated_delivery_time(value: time | None) -> time | None:
    if value is None:
        return None
    if type(value) is not time:
        raise ValueError("brief_scanner_consent_delivery_time_type_invalid")
    if value.tzinfo is not None or value.second or value.microsecond:
        raise ValueError("brief_scanner_consent_delivery_time_invalid")
    return value


def _validated_timezone_name(value: str) -> str:
    if type(value) is not str:
        raise ValueError("brief_scanner_consent_timezone_type_invalid")
    cleaned = value.strip()
    if not cleaned:
        return ""
    if len(cleaned) > 64:
        raise ValueError("brief_scanner_consent_timezone_invalid")
    try:
        ZoneInfo(cleaned)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("brief_scanner_consent_timezone_invalid") from exc
    return cleaned


def plan_brief_scanner_consent(
    bundle: BriefScannerMissionPlanningBundle,
    *,
    memory_consent_active: bool,
    reminder_delivery_time: time | None = None,
    reminder_timezone_name: str = "",
) -> BriefScannerConsentPlan | None:
    """Return a consent plan after validating all non-decision prerequisites.

    Review-only bundles require no mutation consent and return ``None``. Other bundles always
    request Mission creation first, followed by applicable child actions. Missing inputs remain
    explicit and prevent decision recording.
    """
    if type(memory_consent_active) is not bool:
        raise ValueError("brief_scanner_consent_memory_state_type_invalid")
    _require_read_only_bundle(bundle)

    if bundle.mission.kind == BriefScannerMissionKind.REVIEW_ONLY:
        if bundle.draft is not None or bundle.reminder is not None:
            raise ValueError("brief_scanner_consent_bundle_structure_invalid")
        return None

    actions = [BriefScannerConsentAction.CREATE_MISSION]
    missing_inputs: list[BriefScannerConsentInput] = []
    if not memory_consent_active:
        missing_inputs.append(BriefScannerConsentInput.MEMORY_CONSENT)

    if bundle.draft is not None:
        actions.append(BriefScannerConsentAction.GENERATE_DRAFT)
        missing_inputs.extend(
            _DRAFT_INPUT_MAP[item]
            for item in bundle.draft.required_user_inputs
        )

    delivery_time = None
    timezone_name = ""
    if bundle.reminder is not None:
        actions.append(BriefScannerConsentAction.CREATE_REMINDER)
        delivery_time = _validated_delivery_time(reminder_delivery_time)
        timezone_name = _validated_timezone_name(reminder_timezone_name)
        if delivery_time is None:
            missing_inputs.append(BriefScannerConsentInput.REMINDER_DELIVERY_TIME)
        if not timezone_name:
            missing_inputs.append(BriefScannerConsentInput.REMINDER_TIMEZONE)

    return BriefScannerConsentPlan(
        requested_actions=tuple(actions),
        missing_inputs=tuple(missing_inputs),
        memory_consent_active=memory_consent_active,
        planning_fingerprint=brief_scanner_planning_fingerprint(bundle),
        reminder_delivery_time=delivery_time,
        reminder_timezone_name=timezone_name,
    )


def _require_safe_consent_plan(plan: BriefScannerConsentPlan) -> None:
    requested = plan.requested_actions
    unsafe = (
        not plan.requires_explicit_decision
        or plan.allows_execution
        or plan.allows_side_effects
        or not requested
        or len(plan.planning_fingerprint) != 64
        or requested[0] != BriefScannerConsentAction.CREATE_MISSION
        or len(requested) != len(frozenset(requested))
        or (
            not plan.memory_consent_active
            and BriefScannerConsentInput.MEMORY_CONSENT not in plan.missing_inputs
        )
    )
    if BriefScannerConsentAction.CREATE_REMINDER in requested:
        unsafe = unsafe or (
            plan.reminder_delivery_time is None
            and BriefScannerConsentInput.REMINDER_DELIVERY_TIME not in plan.missing_inputs
        ) or (
            not plan.reminder_timezone_name
            and BriefScannerConsentInput.REMINDER_TIMEZONE not in plan.missing_inputs
        )
    if unsafe:
        raise ValueError("brief_scanner_consent_plan_invalid")


def record_brief_scanner_consent(
    plan: BriefScannerConsentPlan,
    decisions: tuple[BriefScannerConsentChoice, ...],
) -> BriefScannerConsentReceipt:
    """Record one complete, explicit decision set without executing approved actions."""
    _require_safe_consent_plan(plan)
    if not plan.ready_for_decision:
        raise ValueError("brief_scanner_consent_inputs_incomplete")
    if type(decisions) is not tuple:
        raise ValueError("brief_scanner_consent_decisions_type_invalid")

    decision_by_action: dict[BriefScannerConsentAction, BriefScannerConsentDecision] = {}
    requested = frozenset(plan.requested_actions)
    for choice in decisions:
        if type(choice) is not BriefScannerConsentChoice:
            raise ValueError("brief_scanner_consent_choice_type_invalid")
        if (
            type(choice.action) is not BriefScannerConsentAction
            or type(choice.decision) is not BriefScannerConsentDecision
        ):
            raise ValueError("brief_scanner_consent_choice_value_invalid")
        if choice.action not in requested:
            raise ValueError("brief_scanner_consent_action_not_requested")
        if choice.action in decision_by_action:
            raise ValueError("brief_scanner_consent_action_duplicate")
        decision_by_action[choice.action] = choice.decision

    if frozenset(decision_by_action) != requested:
        raise ValueError("brief_scanner_consent_decisions_incomplete")

    mission_approved = (
        decision_by_action[BriefScannerConsentAction.CREATE_MISSION]
        == BriefScannerConsentDecision.APPROVE
    )
    if not mission_approved and any(
        decision_by_action[action] == BriefScannerConsentDecision.APPROVE
        for action in plan.requested_actions
        if action != BriefScannerConsentAction.CREATE_MISSION
    ):
        raise ValueError("brief_scanner_consent_dependency_invalid")

    approved = tuple(
        action
        for action in plan.requested_actions
        if decision_by_action[action] == BriefScannerConsentDecision.APPROVE
    )
    declined = tuple(
        action
        for action in plan.requested_actions
        if decision_by_action[action] == BriefScannerConsentDecision.DECLINE
    )
    return BriefScannerConsentReceipt(
        requested_actions=plan.requested_actions,
        approved_actions=approved,
        declined_actions=declined,
        memory_consent_active=plan.memory_consent_active,
        planning_fingerprint=plan.planning_fingerprint,
    )
