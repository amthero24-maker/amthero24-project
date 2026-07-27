"""Deterministic, side-effect-free mission planning for Brief Scanner facts.

The planner converts a validated action proposal into a bounded mission plan. It never writes to a
database, creates a mission, generates a draft, schedules a reminder, or emits telemetry. Execution
must remain behind a later explicit user-confirmation boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from brief_scanner_action_proposal import (
    BriefScannerActionKind,
    BriefScannerActionProposal,
    propose_brief_scanner_action,
)
from brief_scanner_contract import BriefScannerFacts


class BriefScannerMissionKind(StrEnum):
    PREPARE_RESPONSE = "prepare_response"
    TRACK_DEADLINE = "track_deadline"
    TRACK_APPOINTMENT = "track_appointment"
    REVIEW_ONLY = "review_only"


@dataclass(frozen=True)
class BriefScannerMissionPlan:
    kind: BriefScannerMissionKind
    due_date: date | None = None
    requires_confirmation: bool = True
    allows_side_effects: bool = False


def _plan_from_proposal(
    facts: BriefScannerFacts,
    proposal: BriefScannerActionProposal,
) -> BriefScannerMissionPlan:
    if proposal.kind == BriefScannerActionKind.PREPARE_DRAFT:
        return BriefScannerMissionPlan(
            kind=BriefScannerMissionKind.PREPARE_RESPONSE,
            due_date=facts.deadline or facts.appointment_date,
        )
    if proposal.kind == BriefScannerActionKind.OFFER_REMINDER:
        if facts.deadline is not None:
            return BriefScannerMissionPlan(
                kind=BriefScannerMissionKind.TRACK_DEADLINE,
                due_date=facts.deadline,
            )
        return BriefScannerMissionPlan(
            kind=BriefScannerMissionKind.TRACK_APPOINTMENT,
            due_date=facts.appointment_date,
        )
    return BriefScannerMissionPlan(kind=BriefScannerMissionKind.REVIEW_ONLY)


def plan_brief_scanner_mission(facts: BriefScannerFacts) -> BriefScannerMissionPlan | None:
    """Return one conservative read-only mission plan or ``None`` when planning is unsafe.

    The action-proposal safety gate remains the single source of truth for readability, missing pages,
    escalation, and verified-language checks. A plan never authorizes execution and always requires
    explicit user confirmation.
    """
    proposal = propose_brief_scanner_action(facts)
    if proposal is None:
        return None
    return _plan_from_proposal(facts, proposal)
