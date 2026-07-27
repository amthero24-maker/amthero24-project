from __future__ import annotations

from datetime import date

import pytest

from brief_scanner_action_proposal import (
    BriefScannerActionKind,
    propose_brief_scanner_action,
)
from brief_scanner_contract import BriefScannerFacts


def _facts(**overrides) -> BriefScannerFacts:
    values = {
        "language": "de",
        "readable": True,
        "sender_organization": "Synthetic Authority",
    }
    values.update(overrides)
    return BriefScannerFacts(**values)


def test_requested_action_proposes_draft_without_side_effects() -> None:
    proposal = propose_brief_scanner_action(
        _facts(requested_action="send the requested documents", deadline=date(2026, 8, 15))
    )

    assert proposal is not None
    assert proposal.kind == BriefScannerActionKind.PREPARE_DRAFT
    assert proposal.requires_confirmation is True
    assert proposal.allows_side_effects is False


def test_actionable_date_without_requested_action_offers_reminder() -> None:
    proposal = propose_brief_scanner_action(_facts(appointment_date=date(2026, 8, 12)))

    assert proposal is not None
    assert proposal.kind == BriefScannerActionKind.OFFER_REMINDER


def test_readable_document_without_action_signals_is_review_only() -> None:
    proposal = propose_brief_scanner_action(_facts())

    assert proposal is not None
    assert proposal.kind == BriefScannerActionKind.REVIEW_DOCUMENT


@pytest.mark.parametrize(
    "facts",
    [
        _facts(readable=False, uncertainty="image_quality_low"),
        _facts(missing_pages=True),
        _facts(language="fr"),
        _facts(risk_category="court_litigation"),
    ],
)
def test_unsafe_or_unverified_facts_receive_no_action_proposal(facts: BriefScannerFacts) -> None:
    assert propose_brief_scanner_action(facts) is None


def test_invalid_facts_are_rejected_before_proposal() -> None:
    with pytest.raises(ValueError, match="brief_scanner_amount_invalid"):
        propose_brief_scanner_action(_facts(amount_minor=-1))
