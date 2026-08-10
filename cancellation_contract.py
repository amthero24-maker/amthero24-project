"""Deterministic, privacy-safe contract primitives for cancellation journeys.

No model calls, persistence, WhatsApp sends, external actions, or runtime activation
live here. This module only validates synthetic/verified facts and bounded planning.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from typing import Final

SUPPORTED_CANCELLATION_LANGUAGES: Final[frozenset[str]] = frozenset({"de", "ar", "en", "uk", "el"})
_LANGUAGE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
NEXT_POSSIBLE_DATE_WORDING: Final[str] = "zum nächstmöglichen Zeitpunkt"


class CancellationState(StrEnum):
    RECEIVED = "received"
    NEEDS_CLARIFICATION = "needs_clarification"
    DRAFT_READY = "draft_ready"
    WAITING_FOR_USER_REVIEW = "waiting_for_user_review"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    COMPLETED = "completed"
    BLOCKED_OR_ESCALATED = "blocked_or_escalated"


class CancellationEvent(StrEnum):
    CANCELLATION_STARTED = "cancellation_started"
    CLARIFICATION_REQUIRED = "clarification_required"
    CANCELLATION_DRAFT_GENERATED = "cancellation_draft_generated"
    REMINDER_OFFERED = "reminder_offered"
    CONFIRMATION_RECEIVED = "confirmation_received"
    MISSION_COMPLETED = "mission_completed"
    MISSION_BLOCKED_OR_ESCALATED = "mission_blocked_or_escalated"
    USER_FEEDBACK_POSITIVE = "user_feedback_positive"
    USER_FEEDBACK_NEGATIVE = "user_feedback_negative"


HIGH_RISK_CATEGORIES: Final[frozenset[str]] = frozenset({
    "extraordinary_termination_unverified",
    "employment_termination",
    "tenancy_dispute",
    "insurance_dispute",
    "court_deadline",
    "criminal_proceeding",
    "asylum_legal_strategy",
    "deportation_or_detention",
})

ALLOWED_TRANSITIONS: Final[dict[CancellationState, frozenset[CancellationState]]] = {
    CancellationState.RECEIVED: frozenset({
        CancellationState.NEEDS_CLARIFICATION,
        CancellationState.DRAFT_READY,
        CancellationState.BLOCKED_OR_ESCALATED,
    }),
    CancellationState.NEEDS_CLARIFICATION: frozenset({
        CancellationState.DRAFT_READY,
        CancellationState.BLOCKED_OR_ESCALATED,
    }),
    CancellationState.DRAFT_READY: frozenset({
        CancellationState.WAITING_FOR_USER_REVIEW,
        CancellationState.BLOCKED_OR_ESCALATED,
    }),
    CancellationState.WAITING_FOR_USER_REVIEW: frozenset({
        CancellationState.WAITING_FOR_CONFIRMATION,
        CancellationState.BLOCKED_OR_ESCALATED,
    }),
    CancellationState.WAITING_FOR_CONFIRMATION: frozenset({
        CancellationState.COMPLETED,
        CancellationState.BLOCKED_OR_ESCALATED,
    }),
    CancellationState.COMPLETED: frozenset(),
    CancellationState.BLOCKED_OR_ESCALATED: frozenset(),
}


@dataclass(frozen=True)
class CancellationFacts:
    conversation_language: str
    provider: str
    service: str
    reference_number: str = ""
    known_effective_date: date | None = None
    notice_period: str = ""
    confirmation_expected: bool = True
    risk_category: str = ""

    def validate(self) -> None:
        if not _LANGUAGE_PATTERN.fullmatch(self.conversation_language):
            raise ValueError("cancellation_language_invalid")
        if self.conversation_language.split("-", 1)[0] not in SUPPORTED_CANCELLATION_LANGUAGES:
            raise ValueError("cancellation_language_unsupported")

    @property
    def requires_escalation(self) -> bool:
        return self.risk_category.strip().casefold() in HIGH_RISK_CATEGORIES

    @property
    def needs_clarification(self) -> bool:
        return not self.provider.strip() or not self.service.strip()

    @property
    def has_known_timing(self) -> bool:
        return self.known_effective_date is not None or bool(self.notice_period.strip())


@dataclass(frozen=True)
class CancellationDraftInput:
    conversation_language: str
    provider: str
    service: str
    effective_date_wording: str
    reference_number: str = ""
    notice_period: str = ""
    confirmation_request: bool = True


def initial_state(facts: CancellationFacts) -> CancellationState:
    facts.validate()
    if facts.requires_escalation:
        return CancellationState.BLOCKED_OR_ESCALATED
    if facts.needs_clarification:
        return CancellationState.NEEDS_CLARIFICATION
    return CancellationState.DRAFT_READY


def require_transition(current: CancellationState, target: CancellationState) -> CancellationState:
    if current == target:
        return current
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"cancellation_transition_invalid:{current.value}:{target.value}")
    return target


def verified_draft_input(facts: CancellationFacts) -> CancellationDraftInput:
    state = initial_state(facts)
    if state is not CancellationState.DRAFT_READY:
        raise ValueError(f"cancellation_draft_input_unavailable:{state.value}")

    effective_wording = (
        facts.known_effective_date.isoformat()
        if facts.known_effective_date is not None
        else NEXT_POSSIBLE_DATE_WORDING
    )
    return CancellationDraftInput(
        conversation_language=facts.conversation_language,
        provider=facts.provider,
        service=facts.service,
        effective_date_wording=effective_wording,
        reference_number=facts.reference_number.strip(),
        notice_period=facts.notice_period.strip(),
        confirmation_request=facts.confirmation_expected,
    )


_CORRECTABLE_FIELDS: Final[frozenset[str]] = frozenset({
    "provider",
    "service",
    "reference_number",
    "known_effective_date",
    "notice_period",
    "confirmation_expected",
})


def apply_user_correction(facts: CancellationFacts, **verified_changes: object) -> CancellationFacts:
    unknown = set(verified_changes) - _CORRECTABLE_FIELDS
    if unknown:
        raise ValueError("cancellation_correction_field_invalid")
    corrected = replace(facts, **verified_changes)
    corrected.validate()
    return corrected


def reminder_eligible(facts: CancellationFacts) -> bool:
    """A follow-up is eligible only when cancellation confirmation is expected."""
    facts.validate()
    return not facts.requires_escalation and facts.confirmation_expected


def aggregate_events_for_planning(facts: CancellationFacts) -> tuple[CancellationEvent, ...]:
    """Return content-free aggregate telemetry events only."""
    state = initial_state(facts)
    events: list[CancellationEvent] = [CancellationEvent.CANCELLATION_STARTED]
    if state is CancellationState.BLOCKED_OR_ESCALATED:
        events.append(CancellationEvent.MISSION_BLOCKED_OR_ESCALATED)
    elif state is CancellationState.NEEDS_CLARIFICATION:
        events.append(CancellationEvent.CLARIFICATION_REQUIRED)
    else:
        events.append(CancellationEvent.CANCELLATION_DRAFT_GENERATED)
        if reminder_eligible(facts):
            events.append(CancellationEvent.REMINDER_OFFERED)
    return tuple(events)
