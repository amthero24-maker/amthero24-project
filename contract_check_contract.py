"""Deterministic, privacy-safe primitives for ordinary contract-check journeys.

This module does not parse files, call models, persist data, send WhatsApp messages,
or enable runtime actions. It only validates already-extracted/verified facts and
produces bounded review planning for the Vertrags-Check acceptance contract.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from typing import Final

SUPPORTED_CONTRACT_CHECK_LANGUAGES: Final[frozenset[str]] = frozenset({"de", "ar", "en", "uk", "el"})
_LANGUAGE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")


class ContractCheckState(StrEnum):
    RECEIVED = "received"
    NEEDS_CLARIFICATION = "needs_clarification"
    SUMMARY_READY = "summary_ready"
    WAITING_FOR_USER = "waiting_for_user"
    COMPLETED = "completed"
    BLOCKED_OR_ESCALATED = "blocked_or_escalated"


class ContractCheckEvent(StrEnum):
    CONTRACT_CHECK_STARTED = "contract_check_started"
    DOCUMENT_READABLE = "document_readable"
    DOCUMENT_INCOMPLETE = "document_incomplete"
    CONTRACT_SUMMARY_DELIVERED = "contract_summary_delivered"
    DEADLINE_IDENTIFIED = "deadline_identified"
    CLARIFICATION_REQUIRED = "clarification_required"
    NEXT_ACTION_SELECTED = "next_action_selected"
    MISSION_CREATED = "mission_created"
    MISSION_COMPLETED = "mission_completed"
    MISSION_BLOCKED_OR_ESCALATED = "mission_blocked_or_escalated"
    USER_FEEDBACK_POSITIVE = "user_feedback_positive"
    USER_FEEDBACK_NEGATIVE = "user_feedback_negative"


HIGH_RISK_CONTRACT_CATEGORIES: Final[frozenset[str]] = frozenset({
    "litigation_strategy",
    "criminal_proceeding",
    "asylum_legal_strategy",
    "deportation_or_detention",
    "employment_dispute",
    "tenancy_dispute",
    "insurance_dispute",
    "court_deadline",
})

ALLOWED_TRANSITIONS: Final[dict[ContractCheckState, frozenset[ContractCheckState]]] = {
    ContractCheckState.RECEIVED: frozenset({
        ContractCheckState.NEEDS_CLARIFICATION,
        ContractCheckState.SUMMARY_READY,
        ContractCheckState.BLOCKED_OR_ESCALATED,
    }),
    ContractCheckState.NEEDS_CLARIFICATION: frozenset({
        ContractCheckState.SUMMARY_READY,
        ContractCheckState.BLOCKED_OR_ESCALATED,
    }),
    ContractCheckState.SUMMARY_READY: frozenset({
        ContractCheckState.WAITING_FOR_USER,
        ContractCheckState.COMPLETED,
        ContractCheckState.BLOCKED_OR_ESCALATED,
    }),
    ContractCheckState.WAITING_FOR_USER: frozenset({
        ContractCheckState.COMPLETED,
        ContractCheckState.BLOCKED_OR_ESCALATED,
    }),
    ContractCheckState.COMPLETED: frozenset(),
    ContractCheckState.BLOCKED_OR_ESCALATED: frozenset(),
}


@dataclass(frozen=True)
class ContractCheckFacts:
    conversation_language: str
    readable: bool
    complete_enough: bool
    service_or_contract: str = ""
    parties: tuple[str, ...] = ()
    recurring_cost: str = ""
    renewal_date: date | None = None
    cancellation_period: str = ""
    explicit_fee_or_penalty: str = ""
    obligations: tuple[str, ...] = ()
    ambiguous_clause: bool = False
    missing_pages: bool = False
    legal_validity_question: bool = False
    risk_category: str = ""
    follow_up_needed: bool = False

    def validate(self) -> None:
        if not _LANGUAGE_PATTERN.fullmatch(self.conversation_language):
            raise ValueError("contract_check_language_invalid")
        if self.conversation_language.split("-", 1)[0] not in SUPPORTED_CONTRACT_CHECK_LANGUAGES:
            raise ValueError("contract_check_language_unsupported")
        if len(self.parties) > 6 or len(self.obligations) > 12:
            raise ValueError("contract_check_fact_count_exceeded")

    @property
    def requires_escalation(self) -> bool:
        return self.legal_validity_question or self.risk_category.strip().casefold() in HIGH_RISK_CONTRACT_CATEGORIES

    @property
    def has_visible_uncertainty(self) -> bool:
        return self.ambiguous_clause or self.missing_pages or not self.complete_enough

    @property
    def needs_clarification(self) -> bool:
        return not self.readable


@dataclass(frozen=True)
class ContractSummaryInput:
    conversation_language: str
    service_or_contract: str
    parties: tuple[str, ...]
    recurring_cost: str
    renewal_date: date | None
    cancellation_period: str
    explicit_fee_or_penalty: str
    obligations: tuple[str, ...]
    uncertainty: tuple[str, ...]
    legal_interpretation_allowed: bool
    follow_up_needed: bool


def initial_state(facts: ContractCheckFacts) -> ContractCheckState:
    facts.validate()
    if facts.requires_escalation:
        return ContractCheckState.BLOCKED_OR_ESCALATED
    if facts.needs_clarification:
        return ContractCheckState.NEEDS_CLARIFICATION
    return ContractCheckState.SUMMARY_READY


def require_transition(current: ContractCheckState, target: ContractCheckState) -> ContractCheckState:
    if current == target:
        return current
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"contract_check_transition_invalid:{current.value}:{target.value}")
    return target


def verified_summary_input(facts: ContractCheckFacts) -> ContractSummaryInput:
    state = initial_state(facts)
    if state is not ContractCheckState.SUMMARY_READY:
        raise ValueError(f"contract_check_summary_unavailable:{state.value}")

    uncertainty: list[str] = []
    if not facts.complete_enough:
        uncertainty.append("document_incomplete")
    if facts.missing_pages:
        uncertainty.append("missing_pages")
    if facts.ambiguous_clause:
        uncertainty.append("ambiguous_clause")

    return ContractSummaryInput(
        conversation_language=facts.conversation_language,
        service_or_contract=facts.service_or_contract.strip(),
        parties=tuple(item.strip() for item in facts.parties if item.strip()),
        recurring_cost=facts.recurring_cost.strip(),
        renewal_date=facts.renewal_date,
        cancellation_period=facts.cancellation_period.strip(),
        explicit_fee_or_penalty=facts.explicit_fee_or_penalty.strip(),
        obligations=tuple(item.strip() for item in facts.obligations if item.strip()),
        uncertainty=tuple(uncertainty),
        legal_interpretation_allowed=False,
        follow_up_needed=facts.follow_up_needed,
    )


_CORRECTABLE_FIELDS: Final[frozenset[str]] = frozenset({
    "readable",
    "complete_enough",
    "service_or_contract",
    "parties",
    "recurring_cost",
    "renewal_date",
    "cancellation_period",
    "explicit_fee_or_penalty",
    "obligations",
    "ambiguous_clause",
    "missing_pages",
    "follow_up_needed",
})


def apply_user_correction(facts: ContractCheckFacts, **verified_changes: object) -> ContractCheckFacts:
    unexpected = set(verified_changes) - _CORRECTABLE_FIELDS
    if unexpected:
        raise ValueError(f"contract_check_correction_field_invalid:{sorted(unexpected)[0]}")
    corrected = replace(facts, **verified_changes)
    corrected.validate()
    return corrected


def aggregate_events_for_planning(facts: ContractCheckFacts) -> tuple[ContractCheckEvent, ...]:
    state = initial_state(facts)
    events: list[ContractCheckEvent] = [ContractCheckEvent.CONTRACT_CHECK_STARTED]

    if not facts.readable:
        events.extend((
            ContractCheckEvent.DOCUMENT_INCOMPLETE,
            ContractCheckEvent.CLARIFICATION_REQUIRED,
        ))
        return tuple(events)

    events.append(ContractCheckEvent.DOCUMENT_READABLE)

    if state is ContractCheckState.BLOCKED_OR_ESCALATED:
        events.append(ContractCheckEvent.MISSION_BLOCKED_OR_ESCALATED)
        return tuple(events)

    if facts.has_visible_uncertainty:
        events.append(ContractCheckEvent.DOCUMENT_INCOMPLETE)
        events.append(ContractCheckEvent.CLARIFICATION_REQUIRED)

    events.append(ContractCheckEvent.CONTRACT_SUMMARY_DELIVERED)
    if facts.renewal_date is not None:
        events.append(ContractCheckEvent.DEADLINE_IDENTIFIED)
    if facts.follow_up_needed:
        events.extend((ContractCheckEvent.NEXT_ACTION_SELECTED, ContractCheckEvent.MISSION_CREATED))
    return tuple(events)


def privacy_safe_event_names(facts: ContractCheckFacts) -> tuple[str, ...]:
    return tuple(event.value for event in aggregate_events_for_planning(facts))
