"""Deterministic, privacy-safe contract primitives for official writing journeys.

This module contains no model calls, persistence, WhatsApp access, external sending,
or production activation. It only defines validated facts, bounded states, aggregate
events, corrections, and safe planning inputs for the MVP writing acceptance contract.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from typing import Final

SUPPORTED_WRITING_LANGUAGES: Final[frozenset[str]] = frozenset({"de", "ar", "en", "uk", "el"})
SUPPORTED_OUTPUT_LANGUAGES: Final[frozenset[str]] = SUPPORTED_WRITING_LANGUAGES
_LANGUAGE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")


class WritingState(StrEnum):
    RECEIVED = "received"
    NEEDS_CLARIFICATION = "needs_clarification"
    DRAFT_READY = "draft_ready"
    WAITING_FOR_USER_REVIEW = "waiting_for_user_review"
    REVISED = "revised"
    WAITING_FOR_USER_ACTION = "waiting_for_user_action"
    COMPLETED = "completed"
    BLOCKED_OR_ESCALATED = "blocked_or_escalated"


class WritingEvent(StrEnum):
    WRITING_STARTED = "writing_started"
    CLARIFICATION_REQUIRED = "clarification_required"
    DRAFT_GENERATED = "draft_generated"
    DRAFT_REVISED = "draft_revised"
    MISSION_CREATED = "mission_created"
    REMINDER_OFFERED = "reminder_offered"
    MISSION_COMPLETED = "mission_completed"
    USER_FEEDBACK_POSITIVE = "user_feedback_positive"
    USER_FEEDBACK_NEGATIVE = "user_feedback_negative"
    BLOCKED_OR_ESCALATED = "blocked_or_escalated"


HIGH_RISK_CATEGORIES: Final[frozenset[str]] = frozenset({
    "court_litigation",
    "criminal_proceeding",
    "asylum_legal_strategy",
    "deportation_or_detention",
    "medical_emergency",
})

ALLOWED_TRANSITIONS: Final[dict[WritingState, frozenset[WritingState]]] = {
    WritingState.RECEIVED: frozenset({
        WritingState.NEEDS_CLARIFICATION,
        WritingState.DRAFT_READY,
        WritingState.BLOCKED_OR_ESCALATED,
    }),
    WritingState.NEEDS_CLARIFICATION: frozenset({
        WritingState.DRAFT_READY,
        WritingState.BLOCKED_OR_ESCALATED,
    }),
    WritingState.DRAFT_READY: frozenset({
        WritingState.WAITING_FOR_USER_REVIEW,
        WritingState.BLOCKED_OR_ESCALATED,
    }),
    WritingState.WAITING_FOR_USER_REVIEW: frozenset({
        WritingState.REVISED,
        WritingState.WAITING_FOR_USER_ACTION,
        WritingState.BLOCKED_OR_ESCALATED,
    }),
    WritingState.REVISED: frozenset({
        WritingState.WAITING_FOR_USER_REVIEW,
        WritingState.WAITING_FOR_USER_ACTION,
        WritingState.BLOCKED_OR_ESCALATED,
    }),
    WritingState.WAITING_FOR_USER_ACTION: frozenset({
        WritingState.COMPLETED,
        WritingState.BLOCKED_OR_ESCALATED,
    }),
    WritingState.COMPLETED: frozenset(),
    WritingState.BLOCKED_OR_ESCALATED: frozenset(),
}


@dataclass(frozen=True)
class WritingFacts:
    conversation_language: str
    recipient: str
    purpose: str
    output_language: str = "de"
    intent: str = "inquiry"
    reference_number: str = ""
    document_date: date | None = None
    deadline: date | None = None
    amount_minor: int | None = None
    currency: str = "EUR"
    factual_request: str = ""
    ongoing_task: bool = False
    expected_response: bool = False
    risk_category: str = ""

    def validate(self) -> None:
        if not _LANGUAGE_PATTERN.fullmatch(self.conversation_language):
            raise ValueError("writing_conversation_language_invalid")
        if self.conversation_language.split("-", 1)[0] not in SUPPORTED_WRITING_LANGUAGES:
            raise ValueError("writing_conversation_language_unsupported")
        if not _LANGUAGE_PATTERN.fullmatch(self.output_language):
            raise ValueError("writing_output_language_invalid")
        if self.output_language.split("-", 1)[0] not in SUPPORTED_OUTPUT_LANGUAGES:
            raise ValueError("writing_output_language_unsupported")
        if self.amount_minor is not None and self.amount_minor < 0:
            raise ValueError("writing_amount_invalid")
        if self.amount_minor is not None and len(self.currency.strip()) != 3:
            raise ValueError("writing_currency_invalid")

    @property
    def requires_escalation(self) -> bool:
        return self.risk_category.strip().casefold() in HIGH_RISK_CATEGORIES

    @property
    def needs_clarification(self) -> bool:
        return not self.recipient.strip() or not self.purpose.strip()

    @property
    def has_real_follow_up(self) -> bool:
        return self.deadline is not None or self.expected_response


def initial_state(facts: WritingFacts) -> WritingState:
    facts.validate()
    if facts.requires_escalation:
        return WritingState.BLOCKED_OR_ESCALATED
    if facts.needs_clarification:
        return WritingState.NEEDS_CLARIFICATION
    return WritingState.DRAFT_READY


def require_transition(current: WritingState, target: WritingState) -> WritingState:
    """Validate one writing-state transition; replaying the same state is idempotent."""
    if current == target:
        return current
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"writing_transition_invalid:{current.value}:{target.value}")
    return target


def verified_draft_input(facts: WritingFacts) -> dict[str, object]:
    """Return only explicitly supplied, validated facts suitable for a draft boundary."""
    state = initial_state(facts)
    if state is not WritingState.DRAFT_READY:
        raise ValueError(f"writing_draft_input_unavailable:{state.value}")

    verified: dict[str, object] = {
        "conversation_language": facts.conversation_language,
        "output_language": facts.output_language,
        "intent": facts.intent,
        "recipient": facts.recipient,
        "purpose": facts.purpose,
    }
    optional = {
        "reference_number": facts.reference_number.strip(),
        "document_date": facts.document_date,
        "deadline": facts.deadline,
        "amount_minor": facts.amount_minor,
        "currency": facts.currency.strip() if facts.amount_minor is not None else None,
        "factual_request": facts.factual_request.strip(),
    }
    for key, value in optional.items():
        if value not in (None, ""):
            verified[key] = value
    return verified


_CORRECTABLE_FIELDS: Final[frozenset[str]] = frozenset({
    "recipient",
    "purpose",
    "intent",
    "reference_number",
    "document_date",
    "deadline",
    "amount_minor",
    "currency",
    "factual_request",
    "expected_response",
    "ongoing_task",
})


def apply_user_correction(facts: WritingFacts, **verified_changes: object) -> WritingFacts:
    """Replace explicitly corrected fields without retaining superseded values."""
    unknown = set(verified_changes) - _CORRECTABLE_FIELDS
    if unknown:
        raise ValueError("writing_correction_field_invalid")
    corrected = replace(facts, **verified_changes)
    corrected.validate()
    return corrected


def aggregate_events_for_planning(facts: WritingFacts) -> tuple[WritingEvent, ...]:
    """Return content-free aggregate events suitable for privacy-safe product telemetry."""
    state = initial_state(facts)
    events: list[WritingEvent] = [WritingEvent.WRITING_STARTED]
    if state is WritingState.BLOCKED_OR_ESCALATED:
        events.append(WritingEvent.BLOCKED_OR_ESCALATED)
    elif state is WritingState.NEEDS_CLARIFICATION:
        events.append(WritingEvent.CLARIFICATION_REQUIRED)
    else:
        events.append(WritingEvent.DRAFT_GENERATED)
        if facts.ongoing_task:
            events.append(WritingEvent.MISSION_CREATED)
        if facts.has_real_follow_up:
            events.append(WritingEvent.REMINDER_OFFERED)
    return tuple(events)
