from __future__ import annotations

from datetime import date

import pytest

from brief_scanner_contract import (
    BriefScannerEvent,
    BriefScannerFacts,
    BriefScannerState,
    VERIFIED_ACTION_LANGUAGES,
    aggregate_events_for_analysis,
    initial_state,
    require_transition,
)


@pytest.mark.parametrize("language", sorted(VERIFIED_ACTION_LANGUAGES))
def test_readable_document_is_analyzed_in_every_verified_action_language(language: str) -> None:
    facts = BriefScannerFacts(
        language=language,
        readable=True,
        sender_organization="Synthetic Authority",
        deadline=date(2026, 8, 15),
        requested_action="send documents",
    )

    assert initial_state(facts) == BriefScannerState.ANALYZED
    assert aggregate_events_for_analysis(facts) == (
        BriefScannerEvent.SCANNER_STARTED,
        BriefScannerEvent.DOCUMENT_READABLE,
        BriefScannerEvent.SUMMARY_DELIVERED,
    )
    assert facts.language_quality_verified is True
    assert facts.has_actionable_date is True


def test_valid_unverified_language_can_be_analyzed_but_is_not_action_verified() -> None:
    facts = BriefScannerFacts(language="fr", readable=True)

    assert initial_state(facts) == BriefScannerState.ANALYZED
    assert facts.language_quality_verified is False


@pytest.mark.parametrize("language", ["", "f", "french", "de_de", "de-ignore", "DE"])
def test_invalid_language_codes_fail_closed(language: str) -> None:
    with pytest.raises(ValueError, match="brief_scanner_language_code_invalid"):
        BriefScannerFacts(language=language, readable=True).validate()


def test_missing_page_requires_better_document() -> None:
    facts = BriefScannerFacts(language="ar", readable=True, missing_pages=True)

    assert initial_state(facts) == BriefScannerState.NEEDS_BETTER_DOCUMENT
    assert BriefScannerEvent.DOCUMENT_UNREADABLE in aggregate_events_for_analysis(facts)


def test_unreadable_document_requires_non_content_reason() -> None:
    with pytest.raises(ValueError, match="unreadable_document_requires_reason"):
        initial_state(BriefScannerFacts(language="de", readable=False))

    facts = BriefScannerFacts(language="de", readable=False, uncertainty="image_quality_low")
    assert initial_state(facts) == BriefScannerState.NEEDS_BETTER_DOCUMENT


@pytest.mark.parametrize(
    "risk_category",
    [
        "court_litigation",
        "criminal_proceeding",
        "asylum_legal_strategy",
        "deportation_or_detention",
        "medical_emergency",
    ],
)
def test_high_risk_categories_fail_closed_to_escalation(risk_category: str) -> None:
    facts = BriefScannerFacts(language="en", readable=True, risk_category=risk_category)

    assert initial_state(facts) == BriefScannerState.BLOCKED_OR_ESCALATED
    assert aggregate_events_for_analysis(facts) == (
        BriefScannerEvent.SCANNER_STARTED,
        BriefScannerEvent.DOCUMENT_READABLE,
        BriefScannerEvent.MISSION_BLOCKED_OR_ESCALATED,
    )


def test_unreadable_high_risk_document_escalates_without_false_readable_event() -> None:
    facts = BriefScannerFacts(
        language="de",
        readable=False,
        uncertainty="image_quality_low",
        risk_category="court_litigation",
    )

    assert initial_state(facts) == BriefScannerState.BLOCKED_OR_ESCALATED
    assert aggregate_events_for_analysis(facts) == (
        BriefScannerEvent.SCANNER_STARTED,
        BriefScannerEvent.DOCUMENT_UNREADABLE,
        BriefScannerEvent.MISSION_BLOCKED_OR_ESCALATED,
    )


def test_transition_replay_is_idempotent_and_invalid_jump_is_rejected() -> None:
    assert require_transition(BriefScannerState.ANALYZED, BriefScannerState.ANALYZED) == BriefScannerState.ANALYZED
    assert require_transition(BriefScannerState.ANALYZED, BriefScannerState.ACTION_SELECTED) == BriefScannerState.ACTION_SELECTED

    with pytest.raises(ValueError, match="brief_scanner_transition_invalid"):
        require_transition(BriefScannerState.RECEIVED, BriefScannerState.COMPLETED)


def test_terminal_states_cannot_reopen() -> None:
    with pytest.raises(ValueError, match="brief_scanner_transition_invalid"):
        require_transition(BriefScannerState.COMPLETED, BriefScannerState.RECEIVED)
    with pytest.raises(ValueError, match="brief_scanner_transition_invalid"):
        require_transition(BriefScannerState.BLOCKED_OR_ESCALATED, BriefScannerState.ANALYZED)


def test_amount_and_currency_validation() -> None:
    with pytest.raises(ValueError, match="brief_scanner_amount_invalid"):
        BriefScannerFacts(language="de", readable=True, amount_minor=-1).validate()
    with pytest.raises(ValueError, match="brief_scanner_currency_invalid"):
        BriefScannerFacts(language="de", readable=True, amount_minor=100, currency="EURO").validate()


def test_aggregate_events_expose_no_document_or_identity_fields() -> None:
    facts = BriefScannerFacts(
        language="uk",
        readable=True,
        sender_organization="Synthetic Insurer",
        requested_action="reply",
        reference_number="SYNTHETIC-REF-001",
        stated_consequence="synthetic consequence",
    )
    encoded = " ".join(event.value for event in aggregate_events_for_analysis(facts))
    assert "Synthetic Insurer" not in encoded
    assert "SYNTHETIC-REF-001" not in encoded
    assert "synthetic consequence" not in encoded
