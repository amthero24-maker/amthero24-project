"""Pure authorization boundary for approved Brief Scanner plans.

This module converts an immutable read-only planning bundle plus a complete consent receipt into
bounded typed commands for later executors. It never creates a Mission, calls a model, generates a
draft, persists data, schedules a reminder, sends WhatsApp content, or emits telemetry.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from brief_scanner_consent_workflow import (
    BriefScannerConsentAction,
    BriefScannerConsentPlan,
    BriefScannerConsentReceipt,
)
from brief_scanner_draft_planner import BriefScannerDraftKind
from brief_scanner_mission_planner import (
    BriefScannerMissionKind,
    BriefScannerMissionPlanningBundle,
)
from brief_scanner_reminder_planner import BriefScannerReminderKind


class BriefScannerExecutionCommandKind(StrEnum):
    CREATE_MISSION = "create_mission"
    GENERATE_DRAFT = "generate_draft"
    CREATE_REMINDER = "create_reminder"


@dataclass(frozen=True)
class BriefScannerMissionCommand:
    kind: BriefScannerExecutionCommandKind
    mission_kind: BriefScannerMissionKind
    title: str
    topic: str
    next_step: str
    due_date: date | None
    source: str = "brief_scanner"
    authorized: bool = True
    executed: bool = False


@dataclass(frozen=True)
class BriefScannerDraftCommand:
    kind: BriefScannerExecutionCommandKind
    draft_kind: BriefScannerDraftKind
    recipient_organization: str
    response_instruction: str
    document_requested_action: str
    source_language: str
    output_language: str
    due_date: date | None
    reference_number: str
    contact_channel_hint: str
    authorized: bool = True
    executed: bool = False


@dataclass(frozen=True)
class BriefScannerReminderCommand:
    kind: BriefScannerExecutionCommandKind
    reminder_kind: BriefScannerReminderKind
    title: str
    target_date: date
    lead_days: int
    scheduled_at_utc: datetime
    timezone_name: str
    local_delivery_time: time
    source_language: str
    reference_number: str
    authorized: bool = True
    executed: bool = False


@dataclass(frozen=True)
class BriefScannerExecutionEnvelope:
    mission: BriefScannerMissionCommand | None
    draft: BriefScannerDraftCommand | None
    reminder: BriefScannerReminderCommand | None
    approved_actions: tuple[BriefScannerConsentAction, ...]
    declined_actions: tuple[BriefScannerConsentAction, ...]
    requires_executor: bool
    executed: bool = False
    allows_implicit_actions: bool = False

    @property
    def command_count(self) -> int:
        return sum(command is not None for command in (self.mission, self.draft, self.reminder))


def _compact(value: str, *, limit: int) -> str:
    if type(value) is not str:
        raise ValueError("brief_scanner_execution_text_type_invalid")
    if any(ord(character) < 32 and character not in "\t\n\r" for character in value):
        raise ValueError("brief_scanner_execution_control_character")
    return " ".join(value.split()).strip()[:limit]


def _mission_copy(kind: BriefScannerMissionKind, due_date: date | None) -> tuple[str, str]:
    if kind == BriefScannerMissionKind.PREPARE_RESPONSE:
        return "Prepare document response", "Prepare the approved response draft"
    if kind == BriefScannerMissionKind.TRACK_DEADLINE:
        return "Track document deadline", "Complete the required action before the deadline"
    if kind == BriefScannerMissionKind.TRACK_APPOINTMENT:
        return "Track document appointment", "Prepare for the documented appointment"
    raise ValueError("brief_scanner_execution_review_only_not_executable")


def _validate_source_bundle(bundle: BriefScannerMissionPlanningBundle) -> None:
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
            or bundle.reminder.allows_persistence
            or bundle.reminder.allows_scheduling
            or bundle.reminder.allows_side_effects
        )
    if unsafe:
        raise ValueError("brief_scanner_execution_bundle_invalid")


def _validate_consent(
    bundle: BriefScannerMissionPlanningBundle,
    plan: BriefScannerConsentPlan,
    receipt: BriefScannerConsentReceipt,
) -> None:
    expected = [BriefScannerConsentAction.CREATE_MISSION]
    if bundle.draft is not None:
        expected.append(BriefScannerConsentAction.GENERATE_DRAFT)
    if bundle.reminder is not None:
        expected.append(BriefScannerConsentAction.CREATE_REMINDER)
    expected_actions = tuple(expected)

    unsafe = (
        type(plan) is not BriefScannerConsentPlan
        or type(receipt) is not BriefScannerConsentReceipt
        or not plan.ready_for_decision
        or not plan.requires_explicit_decision
        or plan.allows_execution
        or plan.allows_side_effects
        or not plan.memory_consent_active
        or plan.requested_actions != expected_actions
        or receipt.requested_actions != expected_actions
        or not receipt.complete
        or receipt.allows_execution
        or receipt.allows_side_effects
        or not receipt.memory_consent_active
        or len(receipt.approved_actions) != len(frozenset(receipt.approved_actions))
        or len(receipt.declined_actions) != len(frozenset(receipt.declined_actions))
        or frozenset(receipt.approved_actions).intersection(receipt.declined_actions)
        or frozenset(receipt.approved_actions + receipt.declined_actions) != frozenset(expected_actions)
    )
    if BriefScannerConsentAction.CREATE_MISSION not in receipt.approved_actions and any(
        action in receipt.approved_actions
        for action in (
            BriefScannerConsentAction.GENERATE_DRAFT,
            BriefScannerConsentAction.CREATE_REMINDER,
        )
    ):
        unsafe = True
    if unsafe:
        raise ValueError("brief_scanner_execution_consent_invalid")


def _schedule_reminder(
    *,
    target_date: date,
    lead_days: int,
    delivery_time: time,
    timezone_name: str,
    now: datetime,
) -> datetime:
    if type(lead_days) is not int or lead_days < 0 or lead_days > 365:
        raise ValueError("brief_scanner_execution_reminder_lead_invalid")
    if type(delivery_time) is not time or delivery_time.tzinfo is not None:
        raise ValueError("brief_scanner_execution_delivery_time_invalid")
    if delivery_time.second or delivery_time.microsecond:
        raise ValueError("brief_scanner_execution_delivery_time_invalid")
    try:
        timezone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("brief_scanner_execution_timezone_invalid") from exc

    local_date = target_date - timedelta(days=lead_days)
    local_datetime = datetime.combine(local_date, delivery_time, tzinfo=timezone)
    scheduled = local_datetime.astimezone(UTC)
    current = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    if scheduled <= current:
        raise ValueError("brief_scanner_execution_reminder_not_future")
    return scheduled


def build_brief_scanner_execution_envelope(
    bundle: BriefScannerMissionPlanningBundle,
    consent_plan: BriefScannerConsentPlan,
    consent_receipt: BriefScannerConsentReceipt,
    *,
    reminder_lead_days: int | None = None,
    now: datetime | None = None,
) -> BriefScannerExecutionEnvelope:
    """Build typed commands for approved actions without executing any command."""
    _validate_source_bundle(bundle)
    _validate_consent(bundle, consent_plan, consent_receipt)
    approved = frozenset(consent_receipt.approved_actions)

    mission_command = None
    if BriefScannerConsentAction.CREATE_MISSION in approved:
        title, next_step = _mission_copy(bundle.mission.kind, bundle.mission.due_date)
        mission_command = BriefScannerMissionCommand(
            kind=BriefScannerExecutionCommandKind.CREATE_MISSION,
            mission_kind=bundle.mission.kind,
            title=title,
            topic="document",
            next_step=next_step,
            due_date=bundle.mission.due_date,
        )

    draft_command = None
    if BriefScannerConsentAction.GENERATE_DRAFT in approved:
        if mission_command is None or bundle.draft is None:
            raise ValueError("brief_scanner_execution_draft_dependency_invalid")
        draft = bundle.draft
        draft_command = BriefScannerDraftCommand(
            kind=BriefScannerExecutionCommandKind.GENERATE_DRAFT,
            draft_kind=draft.kind,
            recipient_organization=_compact(draft.recipient_organization, limit=160),
            response_instruction=_compact(draft.response_instruction, limit=500),
            document_requested_action=_compact(draft.document_requested_action, limit=500),
            source_language=_compact(draft.source_language, limit=16),
            output_language=_compact(draft.output_language, limit=16),
            due_date=draft.due_date,
            reference_number=_compact(draft.reference_number, limit=120),
            contact_channel_hint=_compact(draft.contact_channel_hint, limit=120),
        )

    reminder_command = None
    if BriefScannerConsentAction.CREATE_REMINDER in approved:
        if mission_command is None or bundle.reminder is None:
            raise ValueError("brief_scanner_execution_reminder_dependency_invalid")
        if reminder_lead_days is None or type(reminder_lead_days) is not int:
            raise ValueError("brief_scanner_execution_reminder_choice_missing")
        reminder = bundle.reminder
        if reminder_lead_days not in reminder.suggested_lead_days:
            raise ValueError("brief_scanner_execution_reminder_choice_invalid")
        delivery_time = consent_plan.reminder_delivery_time
        timezone_name = consent_plan.reminder_timezone_name
        if delivery_time is None or not timezone_name:
            raise ValueError("brief_scanner_execution_reminder_context_missing")
        scheduled = _schedule_reminder(
            target_date=reminder.target_date,
            lead_days=reminder_lead_days,
            delivery_time=delivery_time,
            timezone_name=timezone_name,
            now=now or datetime.now(UTC),
        )
        reminder_command = BriefScannerReminderCommand(
            kind=BriefScannerExecutionCommandKind.CREATE_REMINDER,
            reminder_kind=reminder.kind,
            title=_compact(reminder.title_hint, limit=160) or "Document reminder",
            target_date=reminder.target_date,
            lead_days=reminder_lead_days,
            scheduled_at_utc=scheduled,
            timezone_name=timezone_name,
            local_delivery_time=delivery_time,
            source_language=_compact(reminder.source_language, limit=16),
            reference_number=_compact(reminder.reference_number, limit=120),
        )
    elif reminder_lead_days is not None:
        raise ValueError("brief_scanner_execution_unrequested_reminder_choice")

    return BriefScannerExecutionEnvelope(
        mission=mission_command,
        draft=draft_command,
        reminder=reminder_command,
        approved_actions=consent_receipt.approved_actions,
        declined_actions=consent_receipt.declined_actions,
        requires_executor=mission_command is not None,
    )
