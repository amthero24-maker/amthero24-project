"""Deterministic, side-effect-free draft planning for Brief Scanner facts.

The planner prepares bounded context for a later consent workflow. It does not call a model,
generate text, persist document data, create a mission, schedule a reminder, or emit telemetry.
Even a complete plan never authorizes generation or delivery.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final

from brief_scanner_action_proposal import (
    BriefScannerActionKind,
    propose_brief_scanner_action,
)
from brief_scanner_contract import BriefScannerFacts

MAX_RESPONSE_INSTRUCTION_LENGTH: Final[int] = 500


class BriefScannerDraftKind(StrEnum):
    FORMAL_RESPONSE = "formal_response"


class BriefScannerDraftInput(StrEnum):
    RECIPIENT_ORGANIZATION = "recipient_organization"
    RESPONSE_INSTRUCTION = "response_instruction"


@dataclass(frozen=True)
class BriefScannerDraftPlan:
    kind: BriefScannerDraftKind
    recipient_organization: str
    document_requested_action: str
    response_instruction: str
    source_language: str
    due_date: date | None = None
    reference_number: str = ""
    contact_channel_hint: str = ""
    output_language: str = "de"
    required_user_inputs: tuple[BriefScannerDraftInput, ...] = ()
    requires_confirmation: bool = True
    allows_generation: bool = False
    allows_side_effects: bool = False

    @property
    def ready_for_confirmation(self) -> bool:
        """Return whether the plan has enough context to ask for final confirmation."""
        return not self.required_user_inputs


def _compact(value: str) -> str:
    return " ".join(value.split()).strip()


def _validated_response_instruction(value: str) -> str:
    if type(value) is not str:
        raise ValueError("brief_scanner_response_instruction_type_invalid")
    if any(ord(character) < 32 and character not in "\t\n\r" for character in value):
        raise ValueError("brief_scanner_response_instruction_control_character")
    cleaned = _compact(value)
    if len(cleaned) > MAX_RESPONSE_INSTRUCTION_LENGTH:
        raise ValueError("brief_scanner_response_instruction_too_long")
    return cleaned


def plan_brief_scanner_draft(
    facts: BriefScannerFacts,
    *,
    response_instruction: str = "",
) -> BriefScannerDraftPlan | None:
    """Return a conservative draft plan or ``None`` when drafting is not safely proposed.

    ``response_instruction`` must describe what the user wants the response to say. The action
    requested by the incoming document is retained as source context, but is never treated as the
    user's decision. Missing recipient or response intent remains explicit, and confirmation is
    still mandatory after all required inputs are present.
    """
    proposal = propose_brief_scanner_action(facts)
    if proposal is None or proposal.kind != BriefScannerActionKind.PREPARE_DRAFT:
        return None

    instruction = _validated_response_instruction(response_instruction)
    recipient = _compact(facts.sender_organization)
    required_inputs: list[BriefScannerDraftInput] = []
    if not recipient:
        required_inputs.append(BriefScannerDraftInput.RECIPIENT_ORGANIZATION)
    if not instruction:
        required_inputs.append(BriefScannerDraftInput.RESPONSE_INSTRUCTION)

    return BriefScannerDraftPlan(
        kind=BriefScannerDraftKind.FORMAL_RESPONSE,
        recipient_organization=recipient,
        document_requested_action=_compact(facts.requested_action),
        response_instruction=instruction,
        source_language=facts.language,
        due_date=facts.deadline or facts.appointment_date,
        reference_number=_compact(facts.reference_number),
        contact_channel_hint=_compact(facts.contact_channel),
        required_user_inputs=tuple(required_inputs),
    )
