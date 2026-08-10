from __future__ import annotations

from datetime import date

import pytest

from refund_contract import (
    SUPPORTED_REFUND_LANGUAGES,
    RefundEvent,
    RefundFacts,
    RefundState,
    aggregate_events_for_planning,
    apply_user_correction,
    initial_state,
    privacy_safe_event_names,
    require_transition,
    verified_request_input,
)


@pytest.mark.parametrize("language", sorted(SUPPORTED_REFUND_LANGUAGES))
def test_known_amount_preserved_without_guarantee(language: str) -> None:
    facts = RefundFacts(
        conversation_language=language,
        provider="Synthetic Merchant",
        context="Synthetic purchase was cancelled.",
        amount="49,90 EUR",
        transaction_date=date(2026, 8, 1),
        evidence_available=("synthetic receipt",),
    )
    request = verified_request_input(facts)
    assert request.amount == "49,90 EUR"
    assert request.transaction_date == date(2026, 8, 1)
    assert request.guarantee_allowed is False
    assert initial_state(facts) is RefundState.REQUEST_READY


@pytest.mark.parametrize("language", sorted(SUPPORTED_REFUND_LANGUAGES))
def test_missing_amount_and_date_are_not_invented(language: str) -> None:
    facts = RefundFacts(
        conversation_language=language,
        provider="Synthetic Provider",
        context="Synthetic service problem.",
    )
    request = verified_request_input(facts)
    assert request.amount == ""
    assert request.transaction_date is None
    assert request.missing_evidence_visible is True


@pytest.mark.parametrize("language", sorted(SUPPORTED_REFUND_LANGUAGES))
def test_undelivered_service_and_duplicate_charge_remain_user_facts(language: str) -> None:
    facts = RefundFacts(
        conversation_language=language,
        provider="Synthetic Service",
        context="Synthetic booking was paid but not delivered.",
        amount="25,00 EUR",
        service_status="undelivered",
        duplicate_charge=True,
        evidence_available=("synthetic transaction list",),
    )
    request = verified_request_input(facts)
    assert request.service_status == "undelivered"
    assert request.duplicate_charge is True
    assert request.amount == "25,00 EUR"
    assert request.guarantee_allowed is False


@pytest.mark.parametrize("language", sorted(SUPPORTED_REFUND_LANGUAGES))
def test_guarantee_request_never_enables_guarantee(language: str) -> None:
    facts = RefundFacts(
        conversation_language=language,
        provider="Synthetic Merchant",
        context="Ordinary refund request.",
        guarantee_requested=True,
    )
    assert verified_request_input(facts).guarantee_allowed is False


@pytest.mark.parametrize("language", sorted(SUPPORTED_REFUND_LANGUAGES))
def test_provider_rejection_is_recorded_and_follow_up_offered(language: str) -> None:
    facts = RefundFacts(
        conversation_language=language,
        provider="Synthetic Provider",
        context="Synthetic reimbursement request was rejected.",
        provider_rejected=True,
        response_expected=False,
    )
    events = aggregate_events_for_planning(facts)
    assert RefundEvent.REMINDER_OFFERED in events
    assert RefundEvent.PROVIDER_RESPONSE_RECORDED in events


@pytest.mark.parametrize("language", sorted(SUPPORTED_REFUND_LANGUAGES))
def test_missing_provider_requires_clarification(language: str) -> None:
    facts = RefundFacts(conversation_language=language, provider="", context="Synthetic purchase problem.")
    assert initial_state(facts) is RefundState.NEEDS_CLARIFICATION
    with pytest.raises(ValueError, match="refund_request_unavailable:needs_clarification"):
        verified_request_input(facts)
    assert aggregate_events_for_planning(facts) == (
        RefundEvent.REFUND_HELP_STARTED,
        RefundEvent.CLARIFICATION_REQUIRED,
    )


@pytest.mark.parametrize("language", sorted(SUPPORTED_REFUND_LANGUAGES))
def test_high_risk_financial_or_legal_dispute_fails_closed(language: str) -> None:
    facts = RefundFacts(
        conversation_language=language,
        provider="Synthetic Provider",
        context="Synthetic disputed payment.",
        risk_category="fraud_allegation",
    )
    assert initial_state(facts) is RefundState.BLOCKED_OR_ESCALATED
    assert aggregate_events_for_planning(facts) == (
        RefundEvent.REFUND_HELP_STARTED,
        RefundEvent.MISSION_BLOCKED_OR_ESCALATED,
    )
    with pytest.raises(ValueError, match="refund_request_unavailable:blocked_or_escalated"):
        verified_request_input(facts)


def test_user_correction_replaces_superseded_amount_and_date() -> None:
    original = RefundFacts(
        conversation_language="en",
        provider="Synthetic Merchant",
        context="Synthetic refund request.",
        amount="OLD-SYNTHETIC-AMOUNT",
        transaction_date=date(2026, 7, 1),
    )
    corrected = apply_user_correction(
        original,
        amount="79,00 EUR",
        transaction_date=date(2026, 7, 2),
    )
    request = verified_request_input(corrected)
    assert request.amount == "79,00 EUR"
    assert request.transaction_date == date(2026, 7, 2)
    assert "OLD-SYNTHETIC-AMOUNT" not in repr(corrected)


def test_events_are_aggregate_only_and_do_not_emit_refund_content() -> None:
    facts = RefundFacts(
        conversation_language="de",
        provider="PRIVATE-SYNTHETIC-PROVIDER",
        context="SECRET-SYNTHETIC-CONTEXT",
        amount="SECRET-SYNTHETIC-AMOUNT",
        evidence_available=("SECRET-SYNTHETIC-EVIDENCE",),
    )
    serialized = repr(privacy_safe_event_names(facts))
    assert "PRIVATE-SYNTHETIC" not in serialized
    assert "SECRET-SYNTHETIC" not in serialized


def test_state_transitions_are_bounded_and_idempotent() -> None:
    assert require_transition(RefundState.RECEIVED, RefundState.REQUEST_READY) is RefundState.REQUEST_READY
    assert require_transition(RefundState.REQUEST_READY, RefundState.REQUEST_READY) is RefundState.REQUEST_READY
    with pytest.raises(ValueError, match="refund_transition_invalid"):
        require_transition(RefundState.COMPLETED, RefundState.REQUEST_READY)


def test_unsupported_language_fails_closed() -> None:
    facts = RefundFacts(conversation_language="fr", provider="Synthetic", context="Synthetic")
    with pytest.raises(ValueError, match="refund_language_unsupported"):
        initial_state(facts)
