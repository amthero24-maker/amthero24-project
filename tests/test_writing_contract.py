from __future__ import annotations

from datetime import date

import pytest

from writing_contract import (
    SUPPORTED_WRITING_LANGUAGES,
    WritingEvent,
    WritingFacts,
    WritingState,
    aggregate_events_for_planning,
    apply_user_correction,
    initial_state,
    require_transition,
    verified_draft_input,
)


@pytest.mark.parametrize("language", sorted(SUPPORTED_WRITING_LANGUAGES))
def test_simple_inquiry_is_draft_ready_in_every_supported_language(language: str) -> None:
    facts = WritingFacts(
        conversation_language=language,
        recipient="Synthetic Insurance",
        purpose="Ask which synthetic document is required.",
    )

    assert initial_state(facts) is WritingState.DRAFT_READY
    assert verified_draft_input(facts) == {
        "conversation_language": language,
        "output_language": "de",
        "intent": "inquiry",
        "recipient": "Synthetic Insurance",
        "purpose": "Ask which synthetic document is required.",
    }
    assert aggregate_events_for_planning(facts) == (
        WritingEvent.WRITING_STARTED,
        WritingEvent.DRAFT_GENERATED,
    )


@pytest.mark.parametrize("language", sorted(SUPPORTED_WRITING_LANGUAGES))
def test_reply_preserves_reference_and_document_date_exactly(language: str) -> None:
    facts = WritingFacts(
        conversation_language=language,
        recipient="Synthetic Authority",
        purpose="Reply to the synthetic notice.",
        intent="reply",
        reference_number="SYNTHETIC-REF-182",
        document_date=date(2026, 8, 1),
    )

    verified = verified_draft_input(facts)
    assert verified["reference_number"] == "SYNTHETIC-REF-182"
    assert verified["document_date"] == date(2026, 8, 1)


@pytest.mark.parametrize("language", sorted(SUPPORTED_WRITING_LANGUAGES))
def test_missing_required_recipient_requires_clarification_and_no_draft_event(language: str) -> None:
    facts = WritingFacts(
        conversation_language=language,
        recipient="",
        purpose="Ask for a synthetic appointment.",
    )

    assert initial_state(facts) is WritingState.NEEDS_CLARIFICATION
    assert aggregate_events_for_planning(facts) == (
        WritingEvent.WRITING_STARTED,
        WritingEvent.CLARIFICATION_REQUIRED,
    )
    with pytest.raises(ValueError, match="writing_draft_input_unavailable:needs_clarification"):
        verified_draft_input(facts)


@pytest.mark.parametrize("language", sorted(SUPPORTED_WRITING_LANGUAGES))
def test_real_deadline_is_preserved_and_makes_follow_up_eligible(language: str) -> None:
    facts = WritingFacts(
        conversation_language=language,
        recipient="Synthetic Provider",
        purpose="Request clarification before the stated deadline.",
        deadline=date(2026, 9, 3),
        ongoing_task=True,
    )

    assert facts.has_real_follow_up is True
    assert verified_draft_input(facts)["deadline"] == date(2026, 9, 3)
    assert aggregate_events_for_planning(facts) == (
        WritingEvent.WRITING_STARTED,
        WritingEvent.DRAFT_GENERATED,
        WritingEvent.MISSION_CREATED,
        WritingEvent.REMINDER_OFFERED,
    )


@pytest.mark.parametrize("language", sorted(SUPPORTED_WRITING_LANGUAGES))
def test_no_deadline_or_expected_response_does_not_offer_follow_up(language: str) -> None:
    facts = WritingFacts(
        conversation_language=language,
        recipient="Synthetic School",
        purpose="Send an ordinary informational reply.",
        ongoing_task=True,
    )

    assert facts.has_real_follow_up is False
    assert WritingEvent.REMINDER_OFFERED not in aggregate_events_for_planning(facts)


@pytest.mark.parametrize("language", sorted(SUPPORTED_WRITING_LANGUAGES))
def test_high_risk_legal_writing_fails_closed_to_escalation(language: str) -> None:
    facts = WritingFacts(
        conversation_language=language,
        recipient="Synthetic Court",
        purpose="Prepare litigation strategy.",
        risk_category="court_litigation",
    )

    assert initial_state(facts) is WritingState.BLOCKED_OR_ESCALATED
    assert aggregate_events_for_planning(facts) == (
        WritingEvent.WRITING_STARTED,
        WritingEvent.BLOCKED_OR_ESCALATED,
    )
    with pytest.raises(ValueError, match="writing_draft_input_unavailable:blocked_or_escalated"):
        verified_draft_input(facts)


@pytest.mark.parametrize("language", sorted(SUPPORTED_WRITING_LANGUAGES))
def test_user_correction_replaces_superseded_reference_and_deadline(language: str) -> None:
    original = WritingFacts(
        conversation_language=language,
        recipient="Synthetic Authority",
        purpose="Reply to a notice.",
        reference_number="OLD-SYNTHETIC-REF",
        deadline=date(2026, 8, 20),
    )

    corrected = apply_user_correction(
        original,
        reference_number="NEW-SYNTHETIC-REF",
        deadline=date(2026, 8, 25),
    )
    verified = verified_draft_input(corrected)

    assert corrected.reference_number == "NEW-SYNTHETIC-REF"
    assert corrected.deadline == date(2026, 8, 25)
    assert "OLD-SYNTHETIC-REF" not in repr(corrected)
    assert verified["reference_number"] == "NEW-SYNTHETIC-REF"
    assert verified["deadline"] == date(2026, 8, 25)


def test_verified_draft_input_omits_unknown_optional_facts_instead_of_inventing_them() -> None:
    facts = WritingFacts(
        conversation_language="en",
        recipient="Synthetic Municipality",
        purpose="Ask for information.",
    )

    verified = verified_draft_input(facts)
    assert "reference_number" not in verified
    assert "document_date" not in verified
    assert "deadline" not in verified
    assert "amount_minor" not in verified
    assert "currency" not in verified
    assert "factual_request" not in verified


def test_amount_and_currency_validation_fail_closed() -> None:
    with pytest.raises(ValueError, match="writing_amount_invalid"):
        WritingFacts(
            conversation_language="de",
            recipient="Synthetic Provider",
            purpose="Clarify payment.",
            amount_minor=-1,
        ).validate()

    with pytest.raises(ValueError, match="writing_currency_invalid"):
        WritingFacts(
            conversation_language="de",
            recipient="Synthetic Provider",
            purpose="Clarify payment.",
            amount_minor=100,
            currency="EURO",
        ).validate()


def test_unsupported_languages_fail_closed() -> None:
    with pytest.raises(ValueError, match="writing_conversation_language_unsupported"):
        initial_state(WritingFacts(
            conversation_language="fr",
            recipient="Synthetic Provider",
            purpose="Ask a question.",
        ))
    with pytest.raises(ValueError, match="writing_output_language_unsupported"):
        initial_state(WritingFacts(
            conversation_language="de",
            output_language="fr",
            recipient="Synthetic Provider",
            purpose="Ask a question.",
        ))


def test_corrections_reject_unbounded_fields() -> None:
    facts = WritingFacts(
        conversation_language="de",
        recipient="Synthetic Provider",
        purpose="Ask a question.",
    )
    with pytest.raises(ValueError, match="writing_correction_field_invalid"):
        apply_user_correction(facts, risk_category="court_litigation")


def test_transition_replay_is_idempotent_and_invalid_jump_is_rejected() -> None:
    assert require_transition(WritingState.DRAFT_READY, WritingState.DRAFT_READY) is WritingState.DRAFT_READY
    assert require_transition(WritingState.DRAFT_READY, WritingState.WAITING_FOR_USER_REVIEW) is WritingState.WAITING_FOR_USER_REVIEW
    with pytest.raises(ValueError, match="writing_transition_invalid"):
        require_transition(WritingState.RECEIVED, WritingState.COMPLETED)


def test_terminal_states_cannot_reopen() -> None:
    with pytest.raises(ValueError, match="writing_transition_invalid"):
        require_transition(WritingState.COMPLETED, WritingState.DRAFT_READY)
    with pytest.raises(ValueError, match="writing_transition_invalid"):
        require_transition(WritingState.BLOCKED_OR_ESCALATED, WritingState.NEEDS_CLARIFICATION)


def test_aggregate_events_expose_no_recipient_reference_or_purpose_content() -> None:
    facts = WritingFacts(
        conversation_language="uk",
        recipient="Synthetic Secret Recipient",
        purpose="Synthetic private purpose text",
        reference_number="SYNTHETIC-PRIVATE-REF",
        deadline=date(2026, 9, 5),
        ongoing_task=True,
    )

    encoded = " ".join(event.value for event in aggregate_events_for_planning(facts))
    assert "Synthetic Secret Recipient" not in encoded
    assert "Synthetic private purpose text" not in encoded
    assert "SYNTHETIC-PRIVATE-REF" not in encoded
