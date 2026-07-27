"""Deterministic, side-effect-free action proposals for validated Brief Scanner facts.

The proposal layer converts extracted facts into a bounded next-step offer. It never creates a
mission, reminder, draft, or persistence record. Runtime code must obtain explicit user consent
before executing any proposed action.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from brief_scanner_contract import BriefScannerFacts


class BriefScannerActionKind(StrEnum):
    PREPARE_DRAFT = "prepare_draft"
    OFFER_REMINDER = "offer_reminder"
    REVIEW_DOCUMENT = "review_document"


@dataclass(frozen=True)
class BriefScannerActionProposal:
    kind: BriefScannerActionKind
    requires_confirmation: bool = True
    allows_side_effects: bool = False


def propose_brief_scanner_action(facts: BriefScannerFacts) -> BriefScannerActionProposal | None:
    """Return one conservative proposal for validated, non-escalated facts.

    Draft preparation has priority when the document explicitly requests an action. Otherwise an
    actionable date can justify offering a reminder. A readable document without either signal gets
    a review-only proposal. Unreadable, incomplete, unverified-language, or high-risk documents do
    not receive an automated action proposal.
    """
    facts.validate()
    if (
        not facts.readable
        or facts.missing_pages
        or facts.requires_escalation
        or not facts.language_quality_verified
    ):
        return None
    if facts.requested_action.strip():
        return BriefScannerActionProposal(BriefScannerActionKind.PREPARE_DRAFT)
    if facts.has_actionable_date:
        return BriefScannerActionProposal(BriefScannerActionKind.OFFER_REMINDER)
    return BriefScannerActionProposal(BriefScannerActionKind.REVIEW_DOCUMENT)
