"""Deterministic, privacy-safe contract primitives for the Brief Scanner MVP journey.

No OCR, model calls, persistence, phone numbers, or document content logging live here. This
module defines bounded states, aggregate events, and validation rules for the runtime adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final

SUPPORTED_LANGUAGES: Final[frozenset[str]] = frozenset({"de", "ar", "en", "uk", "el"})


class BriefScannerState(StrEnum):
    RECEIVED = "received"
    NEEDS_BETTER_DOCUMENT = "needs_better_document"
    ANALYZED = "analyzed"
    NEEDS_CLARIFICATION = "needs_clarification"
    ACTION_SELECTED = "action_selected"
    DRAFT_READY = "draft_ready"
    WAITING_FOR_USER_ACTION = "waiting_for_user_action"
    FOLLOW_UP_DUE = "follow_up_due"
    COMPLETED = "completed"
    BLOCKED_OR_ESCALATED = "blocked_or_escalated"


class BriefScannerEvent(StrEnum):
    SCANNER_STARTED = "scanner_started"
    DOCUMENT_READABLE = "document_readable"
    DOCUMENT_UNREADABLE = "document_unreadable"
    SUMMARY_DELIVERED = "summary_delivered"
    CLARIFICATION_REQUIRED = "clarification_required"
    MISSION_CREATED = "mission_created"
    DRAFT_GENERATED = "draft_generated"
    REMINDER_OFFERED = "reminder_offered"
    REMINDER_CREATED = "reminder_created"
    MISSION_COMPLETED = "mission_completed"
    MISSION_BLOCKED_OR_ESCALATED = "mission_blocked_or_escalated"
    USER_FEEDBACK_POSITIVE = "user_feedback_positive"
    USER_FEEDBACK_NEGATIVE = "user_feedback_negative"


ALLOWED_TRANSITIONS: Final[dict[BriefScannerState, frozenset[BriefScannerState]]] = {
    BriefScannerState.RECEIVED: frozenset({BriefScannerState.NEEDS_BETTER_DOCUMENT, BriefScannerState.ANALYZED, BriefScannerState.BLOCKED_OR_ESCALATED}),
    BriefScannerState.NEEDS_BETTER_DOCUMENT: frozenset({BriefScannerState.RECEIVED, BriefScannerState.BLOCKED_OR_ESCALATED}),
    BriefScannerState.ANALYZED: frozenset({BriefScannerState.NEEDS_CLARIFICATION, BriefScannerState.ACTION_SELECTED, BriefScannerState.WAITING_FOR_USER_ACTION, BriefScannerState.BLOCKED_OR_ESCALATED}),
    BriefScannerState.NEEDS_CLARIFICATION: frozenset({BriefScannerState.ANALYZED, BriefScannerState.ACTION_SELECTED, BriefScannerState.BLOCKED_OR_ESCALATED}),
    BriefScannerState.ACTION_SELECTED: frozenset({BriefScannerState.DRAFT_READY, BriefScannerState.WAITING_FOR_USER_ACTION, BriefScannerState.BLOCKED_OR_ESCALATED}),
    BriefScannerState.DRAFT_READY: frozenset({BriefScannerState.WAITING_FOR_USER_ACTION, BriefScannerState.BLOCKED_OR_ESCALATED}),
    BriefScannerState.WAITING_FOR_USER_ACTION: frozenset({BriefScannerState.FOLLOW_UP_DUE, BriefScannerState.COMPLETED, BriefScannerState.BLOCKED_OR_ESCALATED}),
    BriefScannerState.FOLLOW_UP_DUE: frozenset({BriefScannerState.WAITING_FOR_USER_ACTION, BriefScannerState.COMPLETED, BriefScannerState.BLOCKED_OR_ESCALATED}),
    BriefScannerState.COMPLETED: frozenset(),
    BriefScannerState.BLOCKED_OR_ESCALATED: frozenset(),
}

HIGH_RISK_CATEGORIES: Final[frozenset[str]] = frozenset({
    "court_litigation", "criminal_proceeding", "asylum_legal_strategy",
    "deportation_or_detention", "medical_emergency",
})


@dataclass(frozen=True)
class BriefScannerFacts:
    language: str
    readable: bool
    missing_pages: bool = False
    sender_organization: str = ""
    document_date: date | None = None
    deadline: date | None = None
    appointment_date: date | None = None
    requested_action: str = ""
    amount_minor: int | None = None
    currency: str = "EUR"
    stated_consequence: str = ""
    contact_channel: str = ""
    reference_number: str = ""
    risk_category: str = ""
    uncertainty: str = ""

    def validate(self) -> None:
        if self.language not in SUPPORTED_LANGUAGES:
            raise ValueError("unsupported_brief_scanner_language")
        if self.amount_minor is not None and self.amount_minor < 0:
            raise ValueError("brief_scanner_amount_invalid")
        if self.amount_minor is not None and len(self.currency.strip()) != 3:
            raise ValueError("brief_scanner_currency_invalid")
        if not self.readable and not (self.uncertainty.strip() or self.missing_pages):
            raise ValueError("unreadable_document_requires_reason")

    @property
    def requires_escalation(self) -> bool:
        return self.risk_category.strip().casefold() in HIGH_RISK_CATEGORIES

    @property
    def has_actionable_date(self) -> bool:
        return self.deadline is not None or self.appointment_date is not None


def require_transition(current: BriefScannerState, target: BriefScannerState) -> BriefScannerState:
    """Validate one transition; replaying the same state is idempotent."""
    if current == target:
        return current
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"brief_scanner_transition_invalid:{current.value}:{target.value}")
    return target


def initial_state(facts: BriefScannerFacts) -> BriefScannerState:
    facts.validate()
    if facts.requires_escalation:
        return BriefScannerState.BLOCKED_OR_ESCALATED
    if not facts.readable or facts.missing_pages:
        return BriefScannerState.NEEDS_BETTER_DOCUMENT
    return BriefScannerState.ANALYZED


def aggregate_events_for_analysis(facts: BriefScannerFacts) -> tuple[BriefScannerEvent, ...]:
    """Return content-free events suitable for aggregate product telemetry."""
    state = initial_state(facts)
    events: list[BriefScannerEvent] = [BriefScannerEvent.SCANNER_STARTED]
    events.append(BriefScannerEvent.DOCUMENT_UNREADABLE if state == BriefScannerState.NEEDS_BETTER_DOCUMENT else BriefScannerEvent.DOCUMENT_READABLE)
    if state == BriefScannerState.BLOCKED_OR_ESCALATED:
        events.append(BriefScannerEvent.MISSION_BLOCKED_OR_ESCALATED)
    elif state == BriefScannerState.ANALYZED:
        events.append(BriefScannerEvent.SUMMARY_DELIVERED)
    return tuple(events)
