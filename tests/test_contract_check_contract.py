from __future__ import annotations

from datetime import date

import pytest

from contract_check_contract import (
    SUPPORTED_CONTRACT_CHECK_LANGUAGES,
    ContractCheckEvent,
    ContractCheckFacts,
    ContractCheckState,
    aggregate_events_for_planning,
    apply_user_correction,
    initial_state,
    privacy_safe_event_names,
    require_transition,
    verified_summary_input,
)


@pytest.mark.parametrize("language", sorted(SUPPORTED_CONTRACT_CHECK_LANGUAGES))
def test_clear_contract_preserves_renewal_and_recurring_cost(language: str) -> None:
    facts = ContractCheckFacts(
        conversation_language=language,
        readable=True,
        complete_enough=True,
        service_or_contract="Synthetic Internet Contract",
        parties=("Synthetic Customer", "Synthetic Provider"),
        recurring_cost="39,99 EUR monatlich",
        renewal_date=date(2027, 2, 1),
        cancellation_period="3 Monate zum Laufzeitende",
        obligations=("Router nach Vertragsende zurücksenden",),
    )

    assert initial_state(facts) is ContractCheckState.SUMMARY_READY
    summary = verified_summary_input(facts)
    assert summary.recurring_cost == "39,99 EUR monatlich"
    assert summary.renewal_date == date(2027, 2, 1)
    assert summary.cancellation_period == "3 Monate zum Laufzeitende"
    assert summary.legal_interpretation_allowed is False
    assert ContractCheckEvent.DEADLINE_IDENTIFIED in aggregate_events_for_planning(facts)


@pytest.mark.parametrize("language", sorted(SUPPORTED_CONTRACT_CHECK_LANGUAGES))
def test_price_or_fee_clause_is_attributed_exactly_without_legal_conclusion(language: str) -> None:
    facts = ContractCheckFacts(
        conversation_language=language,
        readable=True,
        complete_enough=True,
        service_or_contract="Synthetic Energy Contract",
        recurring_cost="85,00 EUR Abschlag",
        explicit_fee_or_penalty="Preisänderung auf 92,00 EUR ab 01.11.2026",
    )

    summary = verified_summary_input(facts)
    assert summary.recurring_cost == "85,00 EUR Abschlag"
    assert summary.explicit_fee_or_penalty == "Preisänderung auf 92,00 EUR ab 01.11.2026"
    assert summary.legal_interpretation_allowed is False


@pytest.mark.parametrize("language", sorted(SUPPORTED_CONTRACT_CHECK_LANGUAGES))
def test_missing_page_or_ambiguous_clause_stays_visible_as_uncertainty(language: str) -> None:
    facts = ContractCheckFacts(
        conversation_language=language,
        readable=True,
        complete_enough=False,
        service_or_contract="Synthetic Gym Contract",
        missing_pages=True,
        ambiguous_clause=True,
    )

    assert initial_state(facts) is ContractCheckState.SUMMARY_READY
    summary = verified_summary_input(facts)
    assert summary.uncertainty == ("document_incomplete", "missing_pages", "ambiguous_clause")
    events = aggregate_events_for_planning(facts)
    assert ContractCheckEvent.DOCUMENT_INCOMPLETE in events
    assert ContractCheckEvent.CLARIFICATION_REQUIRED in events
    assert ContractCheckEvent.CONTRACT_SUMMARY_DELIVERED in events


@pytest.mark.parametrize("language", sorted(SUPPORTED_CONTRACT_CHECK_LANGUAGES))
def test_unreadable_document_requires_clarification_and_no_summary(language: str) -> None:
    facts = ContractCheckFacts(
        conversation_language=language,
        readable=False,
        complete_enough=False,
        service_or_contract="Synthetic Contract",
    )

    assert initial_state(facts) is ContractCheckState.NEEDS_CLARIFICATION
    with pytest.raises(ValueError, match="contract_check_summary_unavailable:needs_clarification"):
        verified_summary_input(facts)
    assert aggregate_events_for_planning(facts) == (
        ContractCheckEvent.CONTRACT_CHECK_STARTED,
        ContractCheckEvent.DOCUMENT_INCOMPLETE,
        ContractCheckEvent.CLARIFICATION_REQUIRED,
    )


@pytest.mark.parametrize("language", sorted(SUPPORTED_CONTRACT_CHECK_LANGUAGES))
def test_absent_cancellation_information_is_not_invented(language: str) -> None:
    facts = ContractCheckFacts(
        conversation_language=language,
        readable=True,
        complete_enough=True,
        service_or_contract="Synthetic Subscription",
    )

    summary = verified_summary_input(facts)
    assert summary.cancellation_period == ""
    assert summary.renewal_date is None
    assert summary.explicit_fee_or_penalty == ""


@pytest.mark.parametrize("language", sorted(SUPPORTED_CONTRACT_CHECK_LANGUAGES))
def test_legal_validity_question_fails_closed_to_escalation(language: str) -> None:
    facts = ContractCheckFacts(
        conversation_language=language,
        readable=True,
        complete_enough=True,
        service_or_contract="Synthetic Contract",
        legal_validity_question=True,
    )

    assert initial_state(facts) is ContractCheckState.BLOCKED_OR_ESCALATED
    assert aggregate_events_for_planning(facts) == (
        ContractCheckEvent.CONTRACT_CHECK_STARTED,
        ContractCheckEvent.DOCUMENT_READABLE,
        ContractCheckEvent.MISSION_BLOCKED_OR_ESCALATED,
    )
    with pytest.raises(ValueError, match="contract_check_summary_unavailable:blocked_or_escalated"):
        verified_summary_input(facts)


@pytest.mark.parametrize("language", sorted(SUPPORTED_CONTRACT_CHECK_LANGUAGES))
def test_follow_up_creates_mission_only_when_needed(language: str) -> None:
    ordinary = ContractCheckFacts(
        conversation_language=language,
        readable=True,
        complete_enough=True,
        service_or_contract="Synthetic Contract",
        follow_up_needed=False,
    )
    follow_up = apply_user_correction(ordinary, follow_up_needed=True)

    assert ContractCheckEvent.MISSION_CREATED not in aggregate_events_for_planning(ordinary)
    assert aggregate_events_for_planning(follow_up)[-2:] == (
        ContractCheckEvent.NEXT_ACTION_SELECTED,
        ContractCheckEvent.MISSION_CREATED,
    )


def test_user_correction_replaces_superseded_cost_and_renewal() -> None:
    original = ContractCheckFacts(
        conversation_language="en",
        readable=True,
        complete_enough=True,
        recurring_cost="OLD-SYNTHETIC-COST",
        renewal_date=date(2026, 12, 1),
    )
    corrected = apply_user_correction(
        original,
        recurring_cost="49,00 EUR monthly",
        renewal_date=date(2027, 1, 1),
    )

    summary = verified_summary_input(corrected)
    assert summary.recurring_cost == "49,00 EUR monthly"
    assert summary.renewal_date == date(2027, 1, 1)
    assert "OLD-SYNTHETIC-COST" not in repr(corrected)


def test_contract_check_events_are_aggregate_only_and_do_not_emit_document_content() -> None:
    facts = ContractCheckFacts(
        conversation_language="de",
        readable=True,
        complete_enough=True,
        service_or_contract="PRIVATE-SYNTHETIC-SERVICE",
        parties=("PRIVATE-SYNTHETIC-PARTY",),
        recurring_cost="SECRET-SYNTHETIC-AMOUNT",
        cancellation_period="SECRET-SYNTHETIC-CLAUSE",
        obligations=("SECRET-SYNTHETIC-OBLIGATION",),
        follow_up_needed=True,
    )

    names = privacy_safe_event_names(facts)
    serialized = repr(names)
    assert "PRIVATE-SYNTHETIC" not in serialized
    assert "SECRET-SYNTHETIC" not in serialized


def test_state_transitions_are_bounded_and_idempotent() -> None:
    assert require_transition(ContractCheckState.RECEIVED, ContractCheckState.SUMMARY_READY) is ContractCheckState.SUMMARY_READY
    assert require_transition(ContractCheckState.SUMMARY_READY, ContractCheckState.SUMMARY_READY) is ContractCheckState.SUMMARY_READY
    with pytest.raises(ValueError, match="contract_check_transition_invalid"):
        require_transition(ContractCheckState.COMPLETED, ContractCheckState.SUMMARY_READY)


def test_unsupported_language_fails_closed() -> None:
    facts = ContractCheckFacts(
        conversation_language="fr",
        readable=True,
        complete_enough=True,
    )
    with pytest.raises(ValueError, match="contract_check_language_unsupported"):
        initial_state(facts)
