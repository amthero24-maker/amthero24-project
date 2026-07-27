from __future__ import annotations

from datetime import date

from brief_scanner_contract import BriefScannerFacts
from brief_scanner_mission_planner import (
    BriefScannerMissionKind,
    plan_brief_scanner_mission,
)


def _facts(**overrides) -> BriefScannerFacts:
    values = {
        "language": "de",
        "readable": True,
        "sender_organization": "Synthetic Authority",
    }
    values.update(overrides)
    return BriefScannerFacts(**values)


def test_requested_action_plans_response_and_preserves_deadline() -> None:
    plan = plan_brief_scanner_mission(
        _facts(requested_action="send documents", deadline=date(2026, 8, 15))
    )

    assert plan is not None
    assert plan.kind == BriefScannerMissionKind.PREPARE_RESPONSE
    assert plan.due_date == date(2026, 8, 15)
    assert plan.requires_confirmation is True
    assert plan.allows_side_effects is False


def test_deadline_without_requested_action_plans_deadline_tracking() -> None:
    plan = plan_brief_scanner_mission(_facts(deadline=date(2026, 9, 1)))

    assert plan is not None
    assert plan.kind == BriefScannerMissionKind.TRACK_DEADLINE
    assert plan.due_date == date(2026, 9, 1)


def test_appointment_without_deadline_plans_appointment_tracking() -> None:
    plan = plan_brief_scanner_mission(_facts(appointment_date=date(2026, 9, 2)))

    assert plan is not None
    assert plan.kind == BriefScannerMissionKind.TRACK_APPOINTMENT
    assert plan.due_date == date(2026, 9, 2)


def test_plain_readable_document_stays_review_only() -> None:
    plan = plan_brief_scanner_mission(_facts())

    assert plan is not None
    assert plan.kind == BriefScannerMissionKind.REVIEW_ONLY
    assert plan.due_date is None


def test_action_priority_uses_deadline_before_appointment() -> None:
    plan = plan_brief_scanner_mission(
        _facts(
            requested_action="reply",
            deadline=date(2026, 8, 10),
            appointment_date=date(2026, 8, 12),
        )
    )

    assert plan is not None
    assert plan.kind == BriefScannerMissionKind.PREPARE_RESPONSE
    assert plan.due_date == date(2026, 8, 10)


def test_unsafe_or_unverified_documents_never_receive_a_plan() -> None:
    cases = (
        _facts(readable=False, uncertainty="image_quality_low"),
        _facts(missing_pages=True),
        _facts(language="fr"),
        _facts(risk_category="court_litigation"),
    )

    for facts in cases:
        assert plan_brief_scanner_mission(facts) is None
