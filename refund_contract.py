"""Deterministic, privacy-safe primitives for ordinary refund/reimbursement journeys."""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from typing import Final

SUPPORTED_REFUND_LANGUAGES: Final[frozenset[str]] = frozenset({"de", "ar", "en", "uk", "el"})
_LANGUAGE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")
HIGH_RISK_REFUND_CATEGORIES: Final[frozenset[str]] = frozenset({
    "high_value_dispute", "fraud_allegation", "court_proceeding", "debt_enforcement",
    "regulated_financial_advice", "chargeback_legal_strategy",
})


class RefundState(StrEnum):
    RECEIVED = "received"
    NEEDS_CLARIFICATION = "needs_clarification"
    REQUEST_READY = "request_ready"
    WAITING_FOR_REVIEW = "waiting_for_review"
    WAITING_FOR_PROVIDER = "waiting_for_provider"
    COMPLETED = "completed"
    BLOCKED_OR_ESCALATED = "blocked_or_escalated"


class RefundEvent(StrEnum):
    REFUND_HELP_STARTED = "refund_help_started"
    CLARIFICATION_REQUIRED = "clarification_required"
    REFUND_REQUEST_GENERATED = "refund_request_generated"
    REMINDER_OFFERED = "reminder_offered"
    PROVIDER_RESPONSE_RECORDED = "provider_response_recorded"
    REFUND_REPORTED_RECEIVED = "refund_reported_received"
    MISSION_COMPLETED = "mission_completed"
    MISSION_BLOCKED_OR_ESCALATED = "mission_blocked_or_escalated"
    USER_FEEDBACK_POSITIVE = "user_feedback_positive"
    USER_FEEDBACK_NEGATIVE = "user_feedback_negative"


ALLOWED_TRANSITIONS: Final[dict[RefundState, frozenset[RefundState]]] = {
    RefundState.RECEIVED: frozenset({RefundState.NEEDS_CLARIFICATION, RefundState.REQUEST_READY, RefundState.BLOCKED_OR_ESCALATED}),
    RefundState.NEEDS_CLARIFICATION: frozenset({RefundState.REQUEST_READY, RefundState.BLOCKED_OR_ESCALATED}),
    RefundState.REQUEST_READY: frozenset({RefundState.WAITING_FOR_REVIEW, RefundState.BLOCKED_OR_ESCALATED}),
    RefundState.WAITING_FOR_REVIEW: frozenset({RefundState.WAITING_FOR_PROVIDER, RefundState.BLOCKED_OR_ESCALATED}),
    RefundState.WAITING_FOR_PROVIDER: frozenset({RefundState.COMPLETED, RefundState.BLOCKED_OR_ESCALATED}),
    RefundState.COMPLETED: frozenset(),
    RefundState.BLOCKED_OR_ESCALATED: frozenset(),
}


@dataclass(frozen=True)
class RefundFacts:
    conversation_language: str
    provider: str
    context: str
    desired_outcome: str = "refund"
    amount: str = ""
    transaction_date: date | None = None
    service_status: str = ""
    duplicate_charge: bool = False
    evidence_available: tuple[str, ...] = ()
    response_expected: bool = True
    response_deadline: date | None = None
    provider_rejected: bool = False
    guarantee_requested: bool = False
    risk_category: str = ""

    def validate(self) -> None:
        if not _LANGUAGE_PATTERN.fullmatch(self.conversation_language):
            raise ValueError("refund_language_invalid")
        if self.conversation_language.split("-", 1)[0] not in SUPPORTED_REFUND_LANGUAGES:
            raise ValueError("refund_language_unsupported")
        if len(self.evidence_available) > 12:
            raise ValueError("refund_evidence_count_exceeded")

    @property
    def requires_escalation(self) -> bool:
        return self.risk_category.strip().casefold() in HIGH_RISK_REFUND_CATEGORIES

    @property
    def needs_clarification(self) -> bool:
        return not self.provider.strip() or not self.context.strip()

    @property
    def follow_up_eligible(self) -> bool:
        return not self.requires_escalation and (self.response_expected or self.response_deadline is not None or self.provider_rejected)


@dataclass(frozen=True)
class RefundRequestInput:
    conversation_language: str
    provider: str
    context: str
    desired_outcome: str
    amount: str
    transaction_date: date | None
    service_status: str
    duplicate_charge: bool
    evidence_available: tuple[str, ...]
    missing_evidence_visible: bool
    provider_rejected: bool
    guarantee_allowed: bool


def initial_state(facts: RefundFacts) -> RefundState:
    facts.validate()
    if facts.requires_escalation:
        return RefundState.BLOCKED_OR_ESCALATED
    if facts.needs_clarification:
        return RefundState.NEEDS_CLARIFICATION
    return RefundState.REQUEST_READY


def require_transition(current: RefundState, target: RefundState) -> RefundState:
    if current == target:
        return current
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"refund_transition_invalid:{current.value}:{target.value}")
    return target


def verified_request_input(facts: RefundFacts) -> RefundRequestInput:
    state = initial_state(facts)
    if state is not RefundState.REQUEST_READY:
        raise ValueError(f"refund_request_unavailable:{state.value}")
    return RefundRequestInput(
        conversation_language=facts.conversation_language,
        provider=facts.provider.strip(),
        context=facts.context.strip(),
        desired_outcome=facts.desired_outcome.strip() or "refund",
        amount=facts.amount.strip(),
        transaction_date=facts.transaction_date,
        service_status=facts.service_status.strip(),
        duplicate_charge=facts.duplicate_charge,
        evidence_available=tuple(item.strip() for item in facts.evidence_available if item.strip()),
        missing_evidence_visible=not bool(tuple(item.strip() for item in facts.evidence_available if item.strip())),
        provider_rejected=facts.provider_rejected,
        guarantee_allowed=False,
    )


_CORRECTABLE_FIELDS: Final[frozenset[str]] = frozenset({
    "provider", "context", "desired_outcome", "amount", "transaction_date", "service_status",
    "duplicate_charge", "evidence_available", "response_expected", "response_deadline", "provider_rejected",
})


def apply_user_correction(facts: RefundFacts, **verified_changes: object) -> RefundFacts:
    unexpected = set(verified_changes) - _CORRECTABLE_FIELDS
    if unexpected:
        raise ValueError(f"refund_correction_field_invalid:{sorted(unexpected)[0]}")
    corrected = replace(facts, **verified_changes)
    corrected.validate()
    return corrected


def aggregate_events_for_planning(facts: RefundFacts) -> tuple[RefundEvent, ...]:
    state = initial_state(facts)
    events: list[RefundEvent] = [RefundEvent.REFUND_HELP_STARTED]
    if state is RefundState.BLOCKED_OR_ESCALATED:
        events.append(RefundEvent.MISSION_BLOCKED_OR_ESCALATED)
        return tuple(events)
    if state is RefundState.NEEDS_CLARIFICATION:
        events.append(RefundEvent.CLARIFICATION_REQUIRED)
        return tuple(events)
    events.append(RefundEvent.REFUND_REQUEST_GENERATED)
    if facts.follow_up_eligible:
        events.append(RefundEvent.REMINDER_OFFERED)
    if facts.provider_rejected:
        events.append(RefundEvent.PROVIDER_RESPONSE_RECORDED)
    return tuple(events)


def privacy_safe_event_names(facts: RefundFacts) -> tuple[str, ...]:
    return tuple(event.value for event in aggregate_events_for_planning(facts))
