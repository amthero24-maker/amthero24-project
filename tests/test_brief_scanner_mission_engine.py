from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date, time

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
from brief_scanner_draft_planner import BriefScannerDraftInput
from brief_scanner_mission_engine import (
    BriefScannerMissionCommandKind,
    prepare_brief_scanner_mission_execution,
)
from brief_scanner_mission_planner import compose_brief_scanner_mission_plan


def _bundle(*, draft: bool = True, reminder: bool = True):
    facts = BriefScannerFacts(
        language="de",
        readable=True,
        sender_organization="Synthetic Authority",
        requested_action="send documents" if draft else "",
        deadline=date(2026, 8, 15) if reminder else None,
    )
    bundle = compose_brief_scanner_mission_plan(
        facts,
        response_instruction="Ask for a two-week extension." if draft else "",
    )
    assert bundle is not None
    return bundle


def _consent(bundle, decisions):
    plan = plan_brief_scanner_consent(
        bundle,
        memory_consent_active=True,
        reminder_delivery_time=time(9, 30) if bundle.reminder else None,
        reminder_timezone_name="Europe/Berlin" if bundle.reminder else "",
    )
    assert plan is not None
    receipt = record_brief_scanner_consent(
        plan,
        tuple(
            BriefScannerConsentChoice(action=action, decision=decision)
            for action, decision in decisions
        ),
    )
    return plan, receipt


def test_complete_approval_prepares_ordered_inert_commands() -> None:
    bundle = _bundle()
    plan, receipt = _consent(
        bundle,
        (
            (action, BriefScannerConsentDecision.APPROVE)
            for action in (
                BriefScannerConsentAction.CREATE_MISSION,
                BriefScannerConsentAction.GENERATE_DRAFT,
                BriefScannerConsentAction.CREATE_REMINDER,
            )
        ),
    )

    execution = prepare_brief_scanner_mission_execution(bundle, plan, receipt)

    assert tuple(command.kind for command in execution.commands) == (
        BriefScannerMissionCommandKind.CREATE_MISSION,
        BriefScannerMissionCommandKind.GENERATE_DRAFT,
        BriefScannerMissionCommandKind.CREATE_REMINDER,
    )
    assert execution.commands[0].draft is None
    assert execution.commands[1].draft == bundle.draft
    assert execution.commands[2].reminder == bundle.reminder
    assert execution.commands[2].reminder_delivery_time == "09:30"
    assert execution.commands[2].reminder_timezone_name == "Europe/Berlin"
    assert execution.consent_verified is True
    assert execution.allows_persistence is False
    assert execution.allows_generation is False
    assert execution.allows_scheduling is False
    assert execution.allows_side_effects is False
    assert all(command.allows_side_effects is False for command in execution.commands)


def test_declined_child_action_is_not_prepared() -> None:
    bundle = _bundle()
    plan, receipt = _consent(
        bundle,
        (
            (
                BriefScannerConsentAction.CREATE_MISSION,
                BriefScannerConsentDecision.APPROVE,
            ),
            (
                BriefScannerConsentAction.GENERATE_DRAFT,
                BriefScannerConsentDecision.DECLINE,
            ),
            (
                BriefScannerConsentAction.CREATE_REMINDER,
                BriefScannerConsentDecision.APPROVE,
            ),
        ),
    )

    execution = prepare_brief_scanner_mission_execution(bundle, plan, receipt)

    assert tuple(command.kind for command in execution.commands) == (
        BriefScannerMissionCommandKind.CREATE_MISSION,
        BriefScannerMissionCommandKind.CREATE_REMINDER,
    )
    assert execution.declined_actions == (
        BriefScannerConsentAction.GENERATE_DRAFT,
    )


def test_declining_mission_prepares_no_commands() -> None:
    bundle = _bundle(draft=False, reminder=True)
    plan, receipt = _consent(
        bundle,
        (
            (
                BriefScannerConsentAction.CREATE_MISSION,
                BriefScannerConsentDecision.DECLINE,
            ),
            (
                BriefScannerConsentAction.CREATE_REMINDER,
                BriefScannerConsentDecision.DECLINE,
            ),
        ),
    )

    execution = prepare_brief_scanner_mission_execution(bundle, plan, receipt)

    assert execution.commands == ()
    assert execution.declined_actions == receipt.requested_actions


@pytest.mark.parametrize(
    "receipt_change",
    [
        {"complete": False},
        {"allows_execution": True},
        {"allows_side_effects": True},
        {"memory_consent_active": False},
        {"requested_actions": (BriefScannerConsentAction.CREATE_MISSION,)},
        {
            "approved_actions": (
                BriefScannerConsentAction.CREATE_MISSION,
                BriefScannerConsentAction.CREATE_MISSION,
            )
        },
        {
            "approved_actions": (
                BriefScannerConsentAction.GENERATE_DRAFT,
                BriefScannerConsentAction.CREATE_MISSION,
            ),
            "declined_actions": (),
        },
    ],
)
def test_tampered_receipts_fail_closed(receipt_change) -> None:
    bundle = _bundle(draft=True, reminder=False)
    plan, receipt = _consent(
        bundle,
        (
            (
                BriefScannerConsentAction.CREATE_MISSION,
                BriefScannerConsentDecision.APPROVE,
            ),
            (
                BriefScannerConsentAction.GENERATE_DRAFT,
                BriefScannerConsentDecision.APPROVE,
            ),
        ),
    )

    with pytest.raises(ValueError, match="brief_scanner_engine_consent_invalid"):
        prepare_brief_scanner_mission_execution(
            bundle,
            plan,
            replace(receipt, **receipt_change),
        )


def test_receipt_from_different_bundle_is_rejected() -> None:
    draft_bundle = _bundle(draft=True, reminder=False)
    plan, receipt = _consent(
        draft_bundle,
        (
            (
                BriefScannerConsentAction.CREATE_MISSION,
                BriefScannerConsentDecision.APPROVE,
            ),
            (
                BriefScannerConsentAction.GENERATE_DRAFT,
                BriefScannerConsentDecision.APPROVE,
            ),
        ),
    )
    reminder_bundle = _bundle(draft=False, reminder=True)

    with pytest.raises(ValueError, match="brief_scanner_engine_consent_invalid"):
        prepare_brief_scanner_mission_execution(reminder_bundle, plan, receipt)


def test_receipt_cannot_be_replayed_for_same_shape_with_different_content() -> None:
    first_bundle = _bundle(draft=True, reminder=False)
    plan, receipt = _consent(
        first_bundle,
        (
            (
                BriefScannerConsentAction.CREATE_MISSION,
                BriefScannerConsentDecision.APPROVE,
            ),
            (
                BriefScannerConsentAction.GENERATE_DRAFT,
                BriefScannerConsentDecision.APPROVE,
            ),
        ),
    )
    assert first_bundle.draft is not None
    changed_bundle = replace(
        first_bundle,
        draft=replace(
            first_bundle.draft,
            response_instruction="Request a payment plan instead.",
        ),
    )

    with pytest.raises(ValueError, match="brief_scanner_engine_consent_invalid"):
        prepare_brief_scanner_mission_execution(changed_bundle, plan, receipt)


def test_unsafe_or_incomplete_bundle_is_rejected() -> None:
    bundle = _bundle(draft=True, reminder=False)
    plan, receipt = _consent(
        bundle,
        (
            (
                BriefScannerConsentAction.CREATE_MISSION,
                BriefScannerConsentDecision.APPROVE,
            ),
            (
                BriefScannerConsentAction.GENERATE_DRAFT,
                BriefScannerConsentDecision.APPROVE,
            ),
        ),
    )

    with pytest.raises(ValueError, match="brief_scanner_engine_bundle_invalid"):
        prepare_brief_scanner_mission_execution(
            replace(bundle, allows_persistence=True),
            plan,
            receipt,
        )
    assert bundle.draft is not None
    incomplete = replace(bundle, draft=replace(bundle.draft, response_instruction="",
                                                required_user_inputs=(
                                                    BriefScannerDraftInput.RESPONSE_INSTRUCTION,
                                                )))
    with pytest.raises(ValueError, match="brief_scanner_engine_bundle_invalid"):
        prepare_brief_scanner_mission_execution(incomplete, plan, receipt)


def test_execution_plan_is_immutable() -> None:
    bundle = _bundle(draft=True, reminder=False)
    plan, receipt = _consent(
        bundle,
        (
            (
                BriefScannerConsentAction.CREATE_MISSION,
                BriefScannerConsentDecision.APPROVE,
            ),
            (
                BriefScannerConsentAction.GENERATE_DRAFT,
                BriefScannerConsentDecision.APPROVE,
            ),
        ),
    )
    execution = prepare_brief_scanner_mission_execution(bundle, plan, receipt)

    with pytest.raises(FrozenInstanceError):
        execution.allows_side_effects = True  # type: ignore[misc]


def test_forged_dependency_is_rejected_even_when_shape_is_complete() -> None:
    bundle = _bundle(draft=True, reminder=False)
    plan, _ = _consent(
        bundle,
        (
            (
                BriefScannerConsentAction.CREATE_MISSION,
                BriefScannerConsentDecision.APPROVE,
            ),
            (
                BriefScannerConsentAction.GENERATE_DRAFT,
                BriefScannerConsentDecision.DECLINE,
            ),
        ),
    )
    receipt = BriefScannerConsentReceipt(
        requested_actions=plan.requested_actions,
        approved_actions=(BriefScannerConsentAction.GENERATE_DRAFT,),
        declined_actions=(BriefScannerConsentAction.CREATE_MISSION,),
        memory_consent_active=True,
        planning_fingerprint=plan.planning_fingerprint,
    )

    with pytest.raises(ValueError, match="brief_scanner_engine_consent_dependency_invalid"):
        prepare_brief_scanner_mission_execution(bundle, plan, receipt)
