from __future__ import annotations

from datetime import date

import pytest

from cancellation_contract import (
    NEXT_POSSIBLE_DATE_WORDING,
    SUPPORTED_CANCELLATION_LANGUAGES,
    CancellationEvent,
    CancellationFacts,
    CancellationState,
    aggregate_events_for_planning,
    apply_user_correction,
    initial_state,
    reminder_eligible,
    require_transition,
    verified_draft_input,
)


@pytest.mark.parametrize("language", sorted(SUPPORTED_CANCELLATION_LANGUAGES))
def test_known_cancellation_date_is_preserved_in_every_supported_language(language: str) -> None:
    facts = CancellationFacts(
        conversation_language=language,
        provider="Synthetic Telecom",
        service="Synthetic Internet Plan",
        reference_number="SYN-CANCEL-183",
        known_effective_date=date(2026, 10, 31),
    )

    assert initial_state(facts) is CancellationState.DRAFT_READY
    draft = verified_draft_input(facts)
    assert draft.provider == "Synthetic Telecom"
    assert draft.service == "Synthetic Internet Plan"
    assert draft.reference_number == "SYN-CANCEL-183"
    assert draft.effective_date_wording == "2026-10-31"


@pytest.mark.parametrize("language", sorted(SUPPORTED_CANCELLATION_LANGUAGES))
def test_unknown_notice_period_uses_next_possible_date_without_inventing_timing(language: str) -> None:
    facts = CancellationFacts(
        conversation_language=language,
        provider="Synthetic Gym",
        service="Synthetic Membership",
    )

    assert facts.has_known_timing is False
    draft = verified_draft_input(facts)
    assert draft.effective_date_wording == NEXT_POSSIBLE_DATE_WORDING
    assert draft.notice_period == ""


@pytest.mark.parametrize("language", sorted(SUPPORTED_CANCELLATION_LANGUAGES))
def test_missing_provider_requires_clarification_and_no_draft(language: str) -> None:
    facts = CancellationFacts(
        conversation_language=language,
        provider="",
        service="Synthetic Streaming Subscription",
    )

    assert initial_state(facts) is CancellationState.NEEDS_CLARIFICATION
    assert aggregate_events_for_planning(facts) == (
        CancellationEvent.CANCELLATION_STARTED,
        CancellationEvent.CLARIFICATION_REQUIRED,
    )
    with pytest.raises(ValueError, match="cancellation_draft_input_unavailable:needs_clarification"):
        verified_draft_input(facts)


@pytest.mark.parametrize("language", sorted(SUPPORTED_CANCELLATION_LANGUAGES))
def test_unverified_extraordinary_termination_fails_closed(language: str) -> None:
    facts = CancellationFacts(
        conversation_language=language,
        provider="Synthetic Provider",
        service="Synthetic Contract",
        risk_category="extraordinary_termination_unverified",
    )

    assert initial_state(facts) is CancellationState.BLOCKED_OR_ESCALATED
    assert aggregate_events_for_planning(facts) == (
        CancellationEvent.CANCELLATION_STARTED,
        CancellationEvent.MISSION_BLOCKED_OR_ESCALATED,
    )
    with pytest.raises(ValueError, match="cancellation_draft_input_unavailable:blocked_or_escalated"):
        verified_draft_input(facts)


@pytest.mark.parametrize("language", sorted(SUPPORTED_CANCELLATION_LANGUAGES))
def test_confirmation_expected_makes_follow_up_reminder_eligible(language: str) -> None:
    facts = CancellationFacts(
        conversation_language=language,
        provider="Synthetic Club",
        service="Synthetic Membership",
        confirmation_expected=True,
    )

    assert reminder_eligible(facts) is True
    assert aggregate_events_for_planning(facts) == (
        CancellationEvent.CANCELLATION_STARTED,
        CancellationEvent.CANCELLATION_DRAFT_GENERATED,
        CancellationEvent.REMINDER_OFFERED,
    )


@pytest.mark.parametrize("language", sorted(SUPPORTED_CANCELLATION_LANGUAGES))
def test_no_expected_confirmation_does_not_offer_reminder(language: str) -> None:
    facts = CancellationFacts(
        conversation_language=language,
        provider="Synthetic Club",
        service="Synthetic Membership",
        confirmation_expected=False,
    )

    assert reminder_eligible(facts) is False
    assert CancellationEvent.REMINDER_OFFERED not in aggregate_events_for_planning(facts)


@pytest.mark.parametrize("language", sorted(SUPPORTED_CANCELLATION_LANGUAGES))
def test_user_correction_replaces_contract_data(language: str) -> None:
    original = CancellationFacts(
        conversation_language=language,
        provider="Old Synthetic Provider",
        service="Old Synthetic Service",
        reference_number="OLD-SYN-REF",
    )

    corrected = apply_user_correction(
        original,
        provider="New Synthetic Provider",
        service="New Synthetic Service",
        reference_number="NEW-SYN-REF",
    )
    draft = verified_draft_input(corrected)

    assert draft.provider == "New Synthetic Provider"
    assert draft.service == "New Synthetic Service"
    assert draft.reference_number == "NEW-SYN-REF"
    assert "Old Synthetic Provider" not in repr(corrected)
    assert "OLD-SYN-REF" not in repr(corrected)


def test_verified_notice_period_is_preserved_but_not_interpreted() -> None:
    facts = CancellationFacts(
        conversation_language="de",
        provider="Synthetic Provider",
        service="Synthetic Service",
        notice_period="three synthetic months",
    )

    draft = verified_draft_input(facts)
    assert facts.has_known_timing is True
    assert draft.notice_period == "three synthetic months"
    assert draft.effective_date_wording == NEXT_POSSIBLE_DATE_WORDING


def test_high_risk_categories_do_not_offer_reminder() -> None:
    facts = CancellationFacts(
        conversation_language="de",
        provider="Synthetic Landlord",
        service="Synthetic Tenancy",
        risk_category="tenancy_dispute",
        confirmation_expected=True,
    )
    assert reminder_eligible(facts) is False


def test_unsupported_language_fails_closed() -> None:
    with pytest.raises(ValueError, match="cancellation_language_unsupported"):
        initial_state(CancellationFacts(
            conversation_language="fr",
            provider="Synthetic Provider",
            service="Synthetic Service",
        ))


def test_corrections_reject_risk_category_mutation() -> None:
    facts = CancellationFacts(
        conversation_language="de",
        provider="Synthetic Provider",
        service="Synthetic Service",
    )
    with pytest.raises(ValueError, match="cancellation_correction_field_invalid"):
        apply_user_correction(facts, risk_category="court_deadline")


def test_transition_replay_is_idempotent_and_invalid_jump_is_rejected() -> None:
    assert require_transition(CancellationState.DRAFT_READY, CancellationState.DRAFT_READY) is CancellationState.DRAFT_READY
    assert require_transition(CancellationState.DRAFT_READY, CancellationState.WAITING_FOR_USER_REVIEW) is CancellationState.WAITING_FOR_USER_REVIEW
    with pytest.raises(ValueError, match="cancellation_transition_invalid"):
        require_transition(CancellationState.RECEIVED, CancellationState.COMPLETED)


def test_terminal_states_cannot_reopen() -> None:
    with pytest.raises(ValueError, match="cancellation_transition_invalid"):
        require_transition(CancellationState.COMPLETED, CancellationState.DRAFT_READY)
    with pytest.raises(ValueError, match="cancellation_transition_invalid"):
        require_transition(CancellationState.BLOCKED_OR_ESCALATED, CancellationState.NEEDS_CLARIFICATION)


def test_aggregate_events_expose_no_provider_service_or_reference_content() -> None:
    facts = CancellationFacts(
        conversation_language="ar",
        provider="Synthetic Private Provider",
        service="Synthetic Private Service",
        reference_number="SYN-PRIVATE-CANCEL-REF",
    )

    encoded = " ".join(event.value for event in aggregate_events_for_planning(facts))
    assert "Synthetic Private Provider" not in encoded
    assert "Synthetic Private Service" not in encoded
    assert "SYN-PRIVATE-CANCEL-REF" not in encoded
