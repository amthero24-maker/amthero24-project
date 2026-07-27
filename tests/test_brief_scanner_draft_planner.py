from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from brief_scanner_contract import BriefScannerFacts
from brief_scanner_draft_planner import (
    MAX_RESPONSE_INSTRUCTION_LENGTH,
    BriefScannerDraftInput,
    BriefScannerDraftKind,
    plan_brief_scanner_draft,
)


def _facts(**overrides) -> BriefScannerFacts:
    values = {
        "language": "ar",
        "readable": True,
        "sender_organization": "Synthetic Authority",
        "requested_action": "send the requested documents",
    }
    values.update(overrides)
    return BriefScannerFacts(**values)


def test_requested_action_plans_bounded_formal_response_context() -> None:
    plan = plan_brief_scanner_draft(
        _facts(
            deadline=date(2026, 8, 15),
            reference_number="AZ 123",
            contact_channel="email",
        )
    )

    assert plan is not None
    assert plan.kind == BriefScannerDraftKind.FORMAL_RESPONSE
    assert plan.recipient_organization == "Synthetic Authority"
    assert plan.document_requested_action == "send the requested documents"
    assert plan.source_language == "ar"
    assert plan.output_language == "de"
    assert plan.due_date == date(2026, 8, 15)
    assert plan.reference_number == "AZ 123"
    assert plan.contact_channel_hint == "email"
    assert plan.required_user_inputs == (BriefScannerDraftInput.RESPONSE_INSTRUCTION,)
    assert plan.ready_for_confirmation is False
    assert plan.requires_confirmation is True
    assert plan.allows_generation is False
    assert plan.allows_side_effects is False


def test_user_instruction_is_normalized_without_becoming_confirmation() -> None:
    plan = plan_brief_scanner_draft(
        _facts(),
        response_instruction="  Ask for   a two-week extension. \n",
    )

    assert plan is not None
    assert plan.response_instruction == "Ask for a two-week extension."
    assert plan.required_user_inputs == ()
    assert plan.ready_for_confirmation is True
    assert plan.requires_confirmation is True
    assert plan.allows_generation is False
    assert plan.allows_side_effects is False


def test_missing_recipient_and_user_intent_remain_explicit() -> None:
    plan = plan_brief_scanner_draft(_facts(sender_organization=""))

    assert plan is not None
    assert plan.required_user_inputs == (
        BriefScannerDraftInput.RECIPIENT_ORGANIZATION,
        BriefScannerDraftInput.RESPONSE_INSTRUCTION,
    )
    assert plan.ready_for_confirmation is False


def test_deadline_has_priority_over_appointment_for_draft_context() -> None:
    plan = plan_brief_scanner_draft(
        _facts(
            deadline=date(2026, 8, 10),
            appointment_date=date(2026, 8, 12),
        )
    )

    assert plan is not None
    assert plan.due_date == date(2026, 8, 10)


def test_non_draft_or_unsafe_documents_never_receive_a_draft_plan() -> None:
    cases = (
        _facts(requested_action=""),
        _facts(readable=False, uncertainty="image_quality_low"),
        _facts(missing_pages=True),
        _facts(language="fr"),
        _facts(risk_category="court_litigation"),
    )

    for facts in cases:
        assert plan_brief_scanner_draft(facts) is None


@pytest.mark.parametrize(
    ("instruction", "error_code"),
    [
        (None, "brief_scanner_response_instruction_type_invalid"),
        ("x\x00y", "brief_scanner_response_instruction_control_character"),
        (
            "x" * (MAX_RESPONSE_INSTRUCTION_LENGTH + 1),
            "brief_scanner_response_instruction_too_long",
        ),
    ],
)
def test_untrusted_response_instruction_is_strictly_bounded(
    instruction: object,
    error_code: str,
) -> None:
    with pytest.raises(ValueError, match=error_code):
        plan_brief_scanner_draft(_facts(), response_instruction=instruction)  # type: ignore[arg-type]


def test_invalid_facts_are_rejected_before_planning() -> None:
    with pytest.raises(ValueError, match="brief_scanner_amount_invalid"):
        plan_brief_scanner_draft(_facts(amount_minor=-1))


def test_draft_plan_is_immutable() -> None:
    plan = plan_brief_scanner_draft(_facts())

    assert plan is not None
    with pytest.raises(FrozenInstanceError):
        plan.allows_generation = True  # type: ignore[misc]
