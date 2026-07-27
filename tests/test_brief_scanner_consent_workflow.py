from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date, time

import pytest

from brief_scanner_consent_workflow import (
    BriefScannerConsentAction,
    BriefScannerConsentChoice,
    BriefScannerConsentDecision,
    BriefScannerConsentInput,
    plan_brief_scanner_consent,
    record_brief_scanner_consent,
)
from brief_scanner_contract import BriefScannerFacts
from brief_scanner_mission_planner import compose_brief_scanner_mission_plan


def _facts(**overrides) -> BriefScannerFacts:
    values = {
        "language": "de",
        "readable": True,
        "sender_organization": "Synthetic Authority",
    }
    values.update(overrides)
    return BriefScannerFacts(**values)


def _choice(
    action: BriefScannerConsentAction,
    decision: BriefScannerConsentDecision,
) -> BriefScannerConsentChoice:
    return BriefScannerConsentChoice(action=action, decision=decision)


def test_consent_plan_exposes_every_missing_prerequisite() -> None:
    bundle = compose_brief_scanner_mission_plan(
        _facts(
            requested_action="send documents",
            deadline=date(2026, 8, 15),
        )
    )

    assert bundle is not None
    plan = plan_brief_scanner_consent(
        bundle,
        memory_consent_active=False,
    )

    assert plan is not None
    assert plan.requested_actions == (
        BriefScannerConsentAction.CREATE_MISSION,
        BriefScannerConsentAction.GENERATE_DRAFT,
        BriefScannerConsentAction.CREATE_REMINDER,
    )
    assert plan.missing_inputs == (
        BriefScannerConsentInput.MEMORY_CONSENT,
        BriefScannerConsentInput.DRAFT_RESPONSE_INSTRUCTION,
        BriefScannerConsentInput.REMINDER_DELIVERY_TIME,
        BriefScannerConsentInput.REMINDER_TIMEZONE,
    )
    assert plan.ready_for_decision is False
    assert plan.requires_explicit_decision is True
    assert plan.allows_execution is False
    assert plan.allows_side_effects is False


def test_complete_context_is_ready_for_explicit_decisions_only() -> None:
    bundle = compose_brief_scanner_mission_plan(
        _facts(
            requested_action="send documents",
            deadline=date(2026, 8, 15),
        ),
        response_instruction="Ask for a two-week extension.",
    )

    assert bundle is not None
    plan = plan_brief_scanner_consent(
        bundle,
        memory_consent_active=True,
        reminder_delivery_time=time(9, 30),
        reminder_timezone_name="Europe/Berlin",
    )

    assert plan is not None
    assert plan.missing_inputs == ()
    assert plan.ready_for_decision is True
    assert plan.reminder_delivery_time == time(9, 30)
    assert plan.reminder_timezone_name == "Europe/Berlin"
    assert plan.allows_execution is False


def test_complete_explicit_approval_records_non_executing_receipt() -> None:
    bundle = compose_brief_scanner_mission_plan(
        _facts(
            requested_action="send documents",
            deadline=date(2026, 8, 15),
        ),
        response_instruction="Ask for a two-week extension.",
    )
    assert bundle is not None
    plan = plan_brief_scanner_consent(
        bundle,
        memory_consent_active=True,
        reminder_delivery_time=time(9),
        reminder_timezone_name="Europe/Berlin",
    )
    assert plan is not None

    decisions = tuple(
        _choice(action, BriefScannerConsentDecision.APPROVE)
        for action in plan.requested_actions
    )
    receipt = record_brief_scanner_consent(plan, decisions)

    assert receipt.approved_actions == plan.requested_actions
    assert receipt.declined_actions == ()
    assert receipt.all_requested_actions_approved is True
    assert receipt.complete is True
    assert receipt.allows_execution is False
    assert receipt.allows_side_effects is False


def test_declining_mission_requires_declining_every_child_action() -> None:
    bundle = compose_brief_scanner_mission_plan(
        _facts(requested_action="reply"),
        response_instruction="Ask for clarification.",
    )
    assert bundle is not None
    plan = plan_brief_scanner_consent(bundle, memory_consent_active=True)
    assert plan is not None

    conflicting = (
        _choice(
            BriefScannerConsentAction.CREATE_MISSION,
            BriefScannerConsentDecision.DECLINE,
        ),
        _choice(
            BriefScannerConsentAction.GENERATE_DRAFT,
            BriefScannerConsentDecision.APPROVE,
        ),
    )
    with pytest.raises(ValueError, match="brief_scanner_consent_dependency_invalid"):
        record_brief_scanner_consent(plan, conflicting)

    declined = tuple(
        _choice(action, BriefScannerConsentDecision.DECLINE)
        for action in plan.requested_actions
    )
    receipt = record_brief_scanner_consent(plan, declined)
    assert receipt.approved_actions == ()
    assert receipt.declined_actions == plan.requested_actions


def test_incomplete_or_duplicate_decisions_fail_closed() -> None:
    bundle = compose_brief_scanner_mission_plan(
        _facts(appointment_date=date(2026, 9, 2))
    )
    assert bundle is not None
    plan = plan_brief_scanner_consent(
        bundle,
        memory_consent_active=True,
        reminder_delivery_time=time(8),
        reminder_timezone_name="Europe/Berlin",
    )
    assert plan is not None

    mission_choice = _choice(
        BriefScannerConsentAction.CREATE_MISSION,
        BriefScannerConsentDecision.APPROVE,
    )
    with pytest.raises(ValueError, match="brief_scanner_consent_decisions_incomplete"):
        record_brief_scanner_consent(plan, (mission_choice,))
    with pytest.raises(ValueError, match="brief_scanner_consent_action_duplicate"):
        record_brief_scanner_consent(
            plan,
            (mission_choice, mission_choice),
        )


def test_missing_inputs_block_decision_recording() -> None:
    bundle = compose_brief_scanner_mission_plan(
        _facts(requested_action="reply")
    )
    assert bundle is not None
    plan = plan_brief_scanner_consent(
        bundle,
        memory_consent_active=False,
    )
    assert plan is not None

    with pytest.raises(ValueError, match="brief_scanner_consent_inputs_incomplete"):
        record_brief_scanner_consent(plan, ())


def test_review_only_bundle_requires_no_mutation_consent() -> None:
    bundle = compose_brief_scanner_mission_plan(_facts())

    assert bundle is not None
    assert plan_brief_scanner_consent(
        bundle,
        memory_consent_active=False,
    ) is None


@pytest.mark.parametrize(
    ("delivery_time", "timezone_name", "error_code"),
    [
        (time(9, 0, 1), "Europe/Berlin", "brief_scanner_consent_delivery_time_invalid"),
        (time(9), "Not/A-Timezone", "brief_scanner_consent_timezone_invalid"),
    ],
)
def test_invalid_reminder_schedule_context_is_rejected(
    delivery_time: time,
    timezone_name: str,
    error_code: str,
) -> None:
    bundle = compose_brief_scanner_mission_plan(
        _facts(deadline=date(2026, 8, 15))
    )
    assert bundle is not None

    with pytest.raises(ValueError, match=error_code):
        plan_brief_scanner_consent(
            bundle,
            memory_consent_active=True,
            reminder_delivery_time=delivery_time,
            reminder_timezone_name=timezone_name,
        )


def test_non_read_only_bundle_is_rejected() -> None:
    bundle = compose_brief_scanner_mission_plan(
        _facts(requested_action="reply"),
        response_instruction="Ask for clarification.",
    )

    assert bundle is not None
    unsafe_bundle = replace(bundle, allows_side_effects=True)
    with pytest.raises(ValueError, match="brief_scanner_consent_bundle_not_read_only"):
        plan_brief_scanner_consent(
            unsafe_bundle,
            memory_consent_active=True,
        )


def test_manually_unsafe_consent_plan_cannot_record_decisions() -> None:
    bundle = compose_brief_scanner_mission_plan(
        _facts(requested_action="reply"),
        response_instruction="Ask for clarification.",
    )
    assert bundle is not None
    plan = plan_brief_scanner_consent(bundle, memory_consent_active=True)
    assert plan is not None
    unsafe_plan = replace(plan, allows_execution=True)

    with pytest.raises(ValueError, match="brief_scanner_consent_plan_invalid"):
        record_brief_scanner_consent(
            unsafe_plan,
            tuple(
                _choice(action, BriefScannerConsentDecision.APPROVE)
                for action in unsafe_plan.requested_actions
            ),
        )


def test_consent_receipt_is_immutable() -> None:
    bundle = compose_brief_scanner_mission_plan(
        _facts(requested_action="reply"),
        response_instruction="Ask for clarification.",
    )
    assert bundle is not None
    plan = plan_brief_scanner_consent(bundle, memory_consent_active=True)
    assert plan is not None
    receipt = record_brief_scanner_consent(
        plan,
        tuple(
            _choice(action, BriefScannerConsentDecision.APPROVE)
            for action in plan.requested_actions
        ),
    )

    with pytest.raises(FrozenInstanceError):
        receipt.allows_execution = True  # type: ignore[misc]
