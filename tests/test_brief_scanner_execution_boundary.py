from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, time

import pytest

from brief_scanner_consent_workflow import (
    BriefScannerConsentAction,
    BriefScannerConsentChoice,
    BriefScannerConsentDecision,
    BriefScannerConsentReceipt,
    plan_brief_scanner_consent,
    record_brief_scanner_consent,
)
from brief_scanner_contract import BriefScannerFacts
from brief_scanner_execution_boundary import (
    BriefScannerExecutionCommandKind,
    build_brief_scanner_execution_envelope,
)
from brief_scanner_mission_planner import compose_brief_scanner_mission_plan


def _facts(**overrides) -> BriefScannerFacts:
    values = {
        "language": "de",
        "readable": True,
        "sender_organization": "Synthetic Authority",
    }
    values.update(overrides)
    return BriefScannerFacts(**values)


def _approved_receipt(plan, *, decline=()):
    declined = frozenset(decline)
    return record_brief_scanner_consent(
        plan,
        tuple(
            BriefScannerConsentChoice(
                action=action,
                decision=(
                    BriefScannerConsentDecision.DECLINE
                    if action in declined
                    else BriefScannerConsentDecision.APPROVE
                ),
            )
            for action in plan.requested_actions
        ),
    )


def test_approved_bundle_becomes_typed_non_executed_commands() -> None:
    bundle = compose_brief_scanner_mission_plan(
        _facts(
            requested_action="send documents",
            deadline=date(2026, 8, 15),
            reference_number="REF-123",
            contact_channel="email",
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
    receipt = _approved_receipt(plan)

    envelope = build_brief_scanner_execution_envelope(
        bundle,
        plan,
        receipt,
        reminder_lead_days=3,
        now=datetime(2026, 8, 1, 8, tzinfo=UTC),
    )

    assert envelope.command_count == 3
    assert envelope.requires_executor is True
    assert envelope.executed is False
    assert envelope.allows_implicit_actions is False
    assert envelope.planning_fingerprint == plan.planning_fingerprint
    assert envelope.mission is not None
    assert envelope.mission.kind == BriefScannerExecutionCommandKind.CREATE_MISSION
    assert envelope.mission.topic == "document"
    assert envelope.mission.executed is False
    assert envelope.draft is not None
    assert envelope.draft.response_instruction == "Ask for a two-week extension."
    assert envelope.draft.document_requested_action == "send documents"
    assert envelope.reminder is not None
    assert envelope.reminder.lead_days == 3
    assert envelope.reminder.scheduled_at_utc == datetime(2026, 8, 12, 7, 30, tzinfo=UTC)
    assert envelope.reminder.timezone_name == "Europe/Berlin"


def test_declined_child_actions_are_not_invented() -> None:
    bundle = compose_brief_scanner_mission_plan(
        _facts(requested_action="send documents", deadline=date(2026, 9, 1)),
        response_instruction="Confirm receipt only.",
    )
    assert bundle is not None
    plan = plan_brief_scanner_consent(
        bundle,
        memory_consent_active=True,
        reminder_delivery_time=time(10),
        reminder_timezone_name="Europe/Berlin",
    )
    assert plan is not None
    receipt = _approved_receipt(
        plan,
        decline=(
            BriefScannerConsentAction.GENERATE_DRAFT,
            BriefScannerConsentAction.CREATE_REMINDER,
        ),
    )

    envelope = build_brief_scanner_execution_envelope(
        bundle,
        plan,
        receipt,
        now=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert envelope.command_count == 1
    assert envelope.mission is not None
    assert envelope.draft is None
    assert envelope.reminder is None


def test_declining_mission_produces_no_commands() -> None:
    bundle = compose_brief_scanner_mission_plan(
        _facts(deadline=date(2026, 9, 1))
    )
    assert bundle is not None
    plan = plan_brief_scanner_consent(
        bundle,
        memory_consent_active=True,
        reminder_delivery_time=time(9),
        reminder_timezone_name="Europe/Berlin",
    )
    assert plan is not None
    receipt = _approved_receipt(
        plan,
        decline=plan.requested_actions,
    )

    envelope = build_brief_scanner_execution_envelope(
        bundle,
        plan,
        receipt,
        now=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert envelope.command_count == 0
    assert envelope.requires_executor is False


def test_reminder_requires_an_explicit_supported_lead_choice() -> None:
    bundle = compose_brief_scanner_mission_plan(
        _facts(deadline=date(2026, 9, 1))
    )
    assert bundle is not None
    plan = plan_brief_scanner_consent(
        bundle,
        memory_consent_active=True,
        reminder_delivery_time=time(9),
        reminder_timezone_name="Europe/Berlin",
    )
    assert plan is not None
    receipt = _approved_receipt(plan)

    with pytest.raises(ValueError, match="reminder_choice_missing"):
        build_brief_scanner_execution_envelope(
            bundle,
            plan,
            receipt,
            now=datetime(2026, 8, 1, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="reminder_choice_invalid"):
        build_brief_scanner_execution_envelope(
            bundle,
            plan,
            receipt,
            reminder_lead_days=7,
            now=datetime(2026, 8, 1, tzinfo=UTC),
        )


def test_past_reminder_schedule_fails_closed() -> None:
    bundle = compose_brief_scanner_mission_plan(
        _facts(deadline=date(2026, 8, 2))
    )
    assert bundle is not None
    plan = plan_brief_scanner_consent(
        bundle,
        memory_consent_active=True,
        reminder_delivery_time=time(9),
        reminder_timezone_name="Europe/Berlin",
    )
    assert plan is not None
    receipt = _approved_receipt(plan)

    with pytest.raises(ValueError, match="reminder_not_future"):
        build_brief_scanner_execution_envelope(
            bundle,
            plan,
            receipt,
            reminder_lead_days=1,
            now=datetime(2026, 8, 2, 12, tzinfo=UTC),
        )


def test_tampered_receipt_or_source_bundle_is_rejected() -> None:
    bundle = compose_brief_scanner_mission_plan(
        _facts(requested_action="send documents"),
        response_instruction="Ask for confirmation.",
    )
    assert bundle is not None
    plan = plan_brief_scanner_consent(bundle, memory_consent_active=True)
    assert plan is not None
    receipt = _approved_receipt(plan)

    forged = BriefScannerConsentReceipt(
        requested_actions=receipt.requested_actions,
        approved_actions=(BriefScannerConsentAction.CREATE_MISSION,),
        declined_actions=(),
        memory_consent_active=True,
    )
    with pytest.raises(ValueError, match="consent_invalid"):
        build_brief_scanner_execution_envelope(bundle, plan, forged)

    unsafe_bundle = replace(bundle, allows_persistence=True)
    with pytest.raises(ValueError, match="bundle_invalid"):
        build_brief_scanner_execution_envelope(unsafe_bundle, plan, receipt)


def test_consent_cannot_be_replayed_for_same_shape_with_different_content() -> None:
    bundle = compose_brief_scanner_mission_plan(
        _facts(requested_action="send documents"),
        response_instruction="Ask for confirmation.",
    )
    assert bundle is not None
    plan = plan_brief_scanner_consent(bundle, memory_consent_active=True)
    assert plan is not None
    receipt = _approved_receipt(plan)
    assert bundle.draft is not None

    changed_bundle = replace(
        bundle,
        draft=replace(
            bundle.draft,
            response_instruction="Request a payment plan instead.",
        ),
    )

    with pytest.raises(ValueError, match="brief_scanner_engine_consent_invalid"):
        build_brief_scanner_execution_envelope(changed_bundle, plan, receipt)


def test_legacy_receipt_without_fingerprint_is_rejected() -> None:
    bundle = compose_brief_scanner_mission_plan(
        _facts(requested_action="send documents"),
        response_instruction="Ask for confirmation.",
    )
    assert bundle is not None
    plan = plan_brief_scanner_consent(bundle, memory_consent_active=True)
    assert plan is not None
    receipt = replace(_approved_receipt(plan), planning_fingerprint="")

    with pytest.raises(ValueError, match="brief_scanner_engine_consent_invalid"):
        build_brief_scanner_execution_envelope(bundle, plan, receipt)


def test_commands_and_envelope_are_immutable() -> None:
    bundle = compose_brief_scanner_mission_plan(
        _facts(requested_action="send documents"),
        response_instruction="Ask for confirmation.",
    )
    assert bundle is not None
    plan = plan_brief_scanner_consent(bundle, memory_consent_active=True)
    assert plan is not None
    envelope = build_brief_scanner_execution_envelope(bundle, plan, _approved_receipt(plan))

    with pytest.raises(FrozenInstanceError):
        envelope.executed = True
    assert envelope.mission is not None
    with pytest.raises(FrozenInstanceError):
        envelope.mission.title = "tampered"
