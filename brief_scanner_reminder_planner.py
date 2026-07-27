"""Deterministic, side-effect-free reminder planning for Brief Scanner facts.

The planner exposes date-only reminder options for a later consent workflow. It does not choose a
delivery time or timezone, persist data, schedule work, contact the user, or emit telemetry.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final

from brief_scanner_action_proposal import propose_brief_scanner_action
from brief_scanner_contract import BriefScannerFacts

DEADLINE_LEAD_DAYS: Final[tuple[int, ...]] = (3, 1)
APPOINTMENT_LEAD_DAYS: Final[tuple[int, ...]] = (1,)


class BriefScannerReminderKind(StrEnum):
    DEADLINE = "deadline"
    APPOINTMENT = "appointment"


@dataclass(frozen=True)
class BriefScannerReminderPlan:
    kind: BriefScannerReminderKind
    target_date: date
    suggested_lead_days: tuple[int, ...]
    title_hint: str
    source_language: str
    reference_number: str = ""
    requires_confirmation: bool = True
    requires_memory_consent: bool = True
    requires_delivery_time: bool = True
    allows_persistence: bool = False
    allows_scheduling: bool = False
    allows_side_effects: bool = False


def _compact(value: str) -> str:
    return " ".join(value.split()).strip()


def _title_hint(facts: BriefScannerFacts) -> str:
    return _compact(facts.sender_organization) or _compact(facts.requested_action)


def plan_brief_scanner_reminder(
    facts: BriefScannerFacts,
) -> BriefScannerReminderPlan | None:
    """Return one bounded reminder plan or ``None`` when offering a reminder is unsafe.

    The shared action-proposal gate remains the source of truth for readability, completeness,
    escalation, and verified-language checks. A date may be offered alongside a draft plan because
    both belong to the same mission. The returned lead days are only suggestions; explicit consent
    and a delivery time are still required before persistence or scheduling.
    """
    if propose_brief_scanner_action(facts) is None:
        return None

    if facts.deadline is not None:
        kind = BriefScannerReminderKind.DEADLINE
        target_date = facts.deadline
        lead_days = DEADLINE_LEAD_DAYS
    elif facts.appointment_date is not None:
        kind = BriefScannerReminderKind.APPOINTMENT
        target_date = facts.appointment_date
        lead_days = APPOINTMENT_LEAD_DAYS
    else:
        return None

    return BriefScannerReminderPlan(
        kind=kind,
        target_date=target_date,
        suggested_lead_days=lead_days,
        title_hint=_title_hint(facts),
        source_language=facts.language,
        reference_number=_compact(facts.reference_number),
    )
