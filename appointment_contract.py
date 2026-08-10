"""Deterministic, privacy-safe primitives for appointment-assistance journeys."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Final

SUPPORTED_APPOINTMENT_LANGUAGES: Final[frozenset[str]] = frozenset({"de", "ar", "en", "uk", "el"})
_LANGUAGE_PATTERN = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
HIGH_RISK_APPOINTMENT_CATEGORIES: Final[frozenset[str]] = frozenset({
    "medical_emergency", "court_deadline", "detention_or_deportation", "urgent_legal_deadline"
})

class AppointmentState(StrEnum):
    RECEIVED = "received"
    NEEDS_CLARIFICATION = "needs_clarification"
    UNDERSTOOD = "understood"
    PREPARATION_READY = "preparation_ready"
    WAITING_FOR_APPOINTMENT = "waiting_for_appointment"
    FOLLOW_UP_DUE = "follow_up_due"
    COMPLETED = "completed"
    BLOCKED_OR_ESCALATED = "blocked_or_escalated"

class AppointmentEvent(StrEnum):
    APPOINTMENT_HELP_STARTED = "appointment_help_started"
    APPOINTMENT_UNDERSTOOD = "appointment_understood"
    CLARIFICATION_REQUIRED = "clarification_required"
    PREPARATION_CHECKLIST_DELIVERED = "preparation_checklist_delivered"
    REMINDER_OFFERED = "reminder_offered"
    APPOINTMENT_CHANGE_DRAFT_GENERATED = "appointment_change_draft_generated"
    FOLLOW_UP_DUE = "follow_up_due"
    MISSION_COMPLETED = "mission_completed"
    MISSION_BLOCKED_OR_ESCALATED = "mission_blocked_or_escalated"

ALLOWED_TRANSITIONS = {
    AppointmentState.RECEIVED: frozenset({AppointmentState.NEEDS_CLARIFICATION, AppointmentState.UNDERSTOOD, AppointmentState.BLOCKED_OR_ESCALATED}),
    AppointmentState.NEEDS_CLARIFICATION: frozenset({AppointmentState.UNDERSTOOD, AppointmentState.BLOCKED_OR_ESCALATED}),
    AppointmentState.UNDERSTOOD: frozenset({AppointmentState.PREPARATION_READY, AppointmentState.WAITING_FOR_APPOINTMENT, AppointmentState.BLOCKED_OR_ESCALATED}),
    AppointmentState.PREPARATION_READY: frozenset({AppointmentState.WAITING_FOR_APPOINTMENT, AppointmentState.BLOCKED_OR_ESCALATED}),
    AppointmentState.WAITING_FOR_APPOINTMENT: frozenset({AppointmentState.FOLLOW_UP_DUE, AppointmentState.COMPLETED, AppointmentState.BLOCKED_OR_ESCALATED}),
    AppointmentState.FOLLOW_UP_DUE: frozenset({AppointmentState.COMPLETED, AppointmentState.BLOCKED_OR_ESCALATED}),
    AppointmentState.COMPLETED: frozenset(),
    AppointmentState.BLOCKED_OR_ESCALATED: frozenset(),
}

@dataclass(frozen=True)
class AppointmentFacts:
    conversation_language: str
    purpose: str
    starts_at: datetime | None
    location_or_channel: str = ""
    organizer: str = ""
    official_requirements: tuple[str, ...] = ()
    suggested_preparation: tuple[str, ...] = ()
    reminder_requested: bool = False
    reminder_ready: bool = True
    appointment_confirmed: bool = False
    change_requested: str = ""
    risk_category: str = ""

    def validate(self) -> None:
        if not _LANGUAGE_PATTERN.fullmatch(self.conversation_language):
            raise ValueError("appointment_language_invalid")
        if self.conversation_language.split("-", 1)[0] not in SUPPORTED_APPOINTMENT_LANGUAGES:
            raise ValueError("appointment_language_unsupported")
        if len(self.official_requirements) > 20 or len(self.suggested_preparation) > 20:
            raise ValueError("appointment_item_count_exceeded")

    @property
    def requires_escalation(self) -> bool:
        return self.risk_category.strip().casefold() in HIGH_RISK_APPOINTMENT_CATEGORIES

    @property
    def needs_clarification(self) -> bool:
        return not self.purpose.strip() or self.starts_at is None or not self.location_or_channel.strip()

    @property
    def reminder_eligible(self) -> bool:
        return self.reminder_requested and self.reminder_ready and self.starts_at is not None and not self.requires_escalation

@dataclass(frozen=True)
class AppointmentPlan:
    purpose: str
    starts_at: datetime
    location_or_channel: str
    organizer: str
    official_requirements: tuple[str, ...]
    suggested_preparation: tuple[str, ...]
    reminder_eligible: bool
    booking_claim_allowed: bool
    change_draft_review_required: bool


def initial_state(facts: AppointmentFacts) -> AppointmentState:
    facts.validate()
    if facts.requires_escalation:
        return AppointmentState.BLOCKED_OR_ESCALATED
    if facts.needs_clarification:
        return AppointmentState.NEEDS_CLARIFICATION
    return AppointmentState.UNDERSTOOD


def verified_plan(facts: AppointmentFacts) -> AppointmentPlan:
    state = initial_state(facts)
    if state is not AppointmentState.UNDERSTOOD:
        raise ValueError(f"appointment_plan_unavailable:{state.value}")
    assert facts.starts_at is not None
    return AppointmentPlan(
        purpose=facts.purpose.strip(),
        starts_at=facts.starts_at,
        location_or_channel=facts.location_or_channel.strip(),
        organizer=facts.organizer.strip(),
        official_requirements=tuple(x.strip() for x in facts.official_requirements if x.strip()),
        suggested_preparation=tuple(x.strip() for x in facts.suggested_preparation if x.strip()),
        reminder_eligible=facts.reminder_eligible,
        booking_claim_allowed=facts.appointment_confirmed,
        change_draft_review_required=bool(facts.change_requested.strip()),
    )


def require_transition(current: AppointmentState, target: AppointmentState) -> AppointmentState:
    if current == target:
        return current
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"appointment_transition_invalid:{current.value}:{target.value}")
    return target

_CORRECTABLE_FIELDS = frozenset({
    "purpose", "starts_at", "location_or_channel", "organizer", "official_requirements",
    "suggested_preparation", "reminder_requested", "reminder_ready", "appointment_confirmed", "change_requested"
})

def apply_user_correction(facts: AppointmentFacts, **changes: object) -> AppointmentFacts:
    unexpected = set(changes) - _CORRECTABLE_FIELDS
    if unexpected:
        raise ValueError(f"appointment_correction_field_invalid:{sorted(unexpected)[0]}")
    updated = replace(facts, **changes)
    updated.validate()
    return updated


def privacy_safe_event_names() -> tuple[str, ...]:
    return tuple(event.value for event in AppointmentEvent)
