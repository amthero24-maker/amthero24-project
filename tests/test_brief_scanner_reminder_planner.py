from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from brief_scanner_contract import BriefScannerFacts
from brief_scanner_reminder_planner import (
    APPOINTMENT_LEAD_DAYS,
    DEADLINE_LEAD_DAYS,
    BriefScannerReminderKind,
    plan_brief_scanner_reminder,
)


def _facts(**overrides) -> BriefScannerFacts:
    values = {
        "language": "uk",
        "readable": True,
        "sender_organization": "Synthetic Authority",
    }
    values.update(overrides)
    return BriefScannerFacts(**values)


def test_deadline_plans_date_only_consent_gated_reminder_options() -> None:
    plan = plan_brief_scanner_reminder(
        _facts(
            deadline=date(2026, 8, 15),
            reference_number="AZ 123",
        )
    )

    assert plan is not None
    assert plan.kind == BriefScannerReminderKind.DEADLINE
    assert plan.target_date == date(2026, 8, 15)
    assert plan.suggested_lead_days == DEADLINE_LEAD_DAYS == (3, 1)
    assert plan.title_hint == "Synthetic Authority"
    assert plan.source_language == "uk"
    assert plan.reference_number == "AZ 123"
    assert plan.requires_confirmation is True
    assert plan.requires_memory_consent is True
    assert plan.requires_delivery_time is True
    assert plan.allows_persistence is False
    assert plan.allows_scheduling is False
    assert plan.allows_side_effects is False


def test_appointment_uses_one_day_option_without_inventing_schedule() -> None:
    plan = plan_brief_scanner_reminder(
        _facts(appointment_date=date(2026, 8, 20))
    )

    assert plan is not None
    assert plan.kind == BriefScannerReminderKind.APPOINTMENT
    assert plan.target_date == date(2026, 8, 20)
    assert plan.suggested_lead_days == APPOINTMENT_LEAD_DAYS == (1,)
    assert not hasattr(plan, "scheduled_at")
    assert not hasattr(plan, "timezone_name")


def test_deadline_has_priority_over_appointment() -> None:
    plan = plan_brief_scanner_reminder(
        _facts(
            deadline=date(2026, 8, 10),
            appointment_date=date(2026, 8, 12),
        )
    )

    assert plan is not None
    assert plan.kind == BriefScannerReminderKind.DEADLINE
    assert plan.target_date == date(2026, 8, 10)


def test_reminder_can_accompany_draft_plan_inside_same_mission() -> None:
    plan = plan_brief_scanner_reminder(
        _facts(
            requested_action="send documents",
            deadline=date(2026, 9, 1),
        )
    )

    assert plan is not None
    assert plan.kind == BriefScannerReminderKind.DEADLINE
    assert plan.target_date == date(2026, 9, 1)
    assert plan.allows_scheduling is False


def test_requested_action_is_only_a_fallback_title_hint() -> None:
    plan = plan_brief_scanner_reminder(
        _facts(
            sender_organization="",
            requested_action="send documents",
            deadline=date(2026, 9, 1),
        )
    )

    assert plan is not None
    assert plan.title_hint == "send documents"


def test_missing_date_or_unsafe_document_never_receives_reminder_plan() -> None:
    cases = (
        _facts(),
        _facts(deadline=date(2026, 8, 15), readable=False, uncertainty="image_quality_low"),
        _facts(deadline=date(2026, 8, 15), missing_pages=True),
        _facts(deadline=date(2026, 8, 15), language="fr"),
        _facts(deadline=date(2026, 8, 15), risk_category="court_litigation"),
    )

    for facts in cases:
        assert plan_brief_scanner_reminder(facts) is None


def test_invalid_facts_are_rejected_before_planning() -> None:
    with pytest.raises(ValueError, match="brief_scanner_amount_invalid"):
        plan_brief_scanner_reminder(_facts(deadline=date(2026, 8, 15), amount_minor=-1))


def test_reminder_plan_is_immutable() -> None:
    plan = plan_brief_scanner_reminder(_facts(deadline=date(2026, 8, 15)))

    assert plan is not None
    with pytest.raises(FrozenInstanceError):
        plan.allows_scheduling = True  # type: ignore[misc]
