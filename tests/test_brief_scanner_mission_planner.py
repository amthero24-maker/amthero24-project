from __future__ import annotations

from datetime import date

from brief_scanner_contract import BriefScannerFacts
from brief_scanner_draft_planner import BriefScannerDraftInput
from brief_scanner_mission_planner import (
    BriefScannerMissionKind,
    compose_brief_scanner_mission_plan,
    plan_brief_scanner_mission,
)
from brief_scanner_reminder_planner import BriefScannerReminderKind


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


def test_composition_keeps_draft_and_reminder_inside_one_mission() -> None:
    bundle = compose_brief_scanner_mission_plan(
        _facts(
            requested_action="send documents",
            deadline=date(2026, 8, 15),
            reference_number="AZ 123",
        ),
        response_instruction="Ask for a two-week extension.",
    )

    assert bundle is not None
    assert bundle.mission.kind == BriefScannerMissionKind.PREPARE_RESPONSE
    assert bundle.mission.due_date == date(2026, 8, 15)
    assert bundle.draft is not None
    assert bundle.draft.response_instruction == "Ask for a two-week extension."
    assert bundle.draft.required_user_inputs == ()
    assert bundle.reminder is not None
    assert bundle.reminder.kind == BriefScannerReminderKind.DEADLINE
    assert bundle.reminder.target_date == bundle.mission.due_date
    assert bundle.requires_confirmation is True
    assert bundle.allows_generation is False
    assert bundle.allows_persistence is False
    assert bundle.allows_scheduling is False
    assert bundle.allows_side_effects is False


def test_composition_keeps_missing_draft_intent_explicit() -> None:
    bundle = compose_brief_scanner_mission_plan(
        _facts(requested_action="send documents")
    )

    assert bundle is not None
    assert bundle.draft is not None
    assert bundle.draft.required_user_inputs == (
        BriefScannerDraftInput.RESPONSE_INSTRUCTION,
    )
    assert bundle.reminder is None


def test_date_only_composition_has_reminder_without_draft() -> None:
    bundle = compose_brief_scanner_mission_plan(
        _facts(appointment_date=date(2026, 9, 2))
    )

    assert bundle is not None
    assert bundle.mission.kind == BriefScannerMissionKind.TRACK_APPOINTMENT
    assert bundle.draft is None
    assert bundle.reminder is not None
    assert bundle.reminder.kind == BriefScannerReminderKind.APPOINTMENT
    assert bundle.reminder.target_date == date(2026, 9, 2)


def test_review_only_composition_has_no_executable_child_plan() -> None:
    bundle = compose_brief_scanner_mission_plan(_facts())

    assert bundle is not None
    assert bundle.mission.kind == BriefScannerMissionKind.REVIEW_ONLY
    assert bundle.draft is None
    assert bundle.reminder is None
    assert bundle.allows_side_effects is False


def test_unsafe_document_never_receives_composed_plan() -> None:
    bundle = compose_brief_scanner_mission_plan(
        _facts(
            requested_action="reply",
            deadline=date(2026, 8, 15),
            risk_category="court_litigation",
        ),
        response_instruction="Dispute the decision.",
    )

    assert bundle is None
