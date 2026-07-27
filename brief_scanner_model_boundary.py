"""Prompt and result boundary for Brief Scanner model extraction.

This module does not call a provider or mutate runtime state. It builds a deterministic extraction
prompt, validates the returned JSON through the strict adapter, and maps failures to bounded,
content-free outcomes that callers can handle without creating missions, reminders, or telemetry.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from brief_scanner_adapter import BriefScannerAdapterError, parse_brief_scanner_model_output
from brief_scanner_contract import BriefScannerFacts, BriefScannerState, initial_state

_RESPONSE_LANGUAGE_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z]{2,3}(?:-[A-Z]{2})?$")


class BriefScannerBoundaryStatus(StrEnum):
    VALIDATED = "validated"
    VALIDATED_READ_ONLY = "validated_read_only"
    RETRYABLE_DOCUMENT_QUALITY = "retryable_document_quality"
    RETRYABLE_MODEL_OUTPUT = "retryable_model_output"
    BLOCKED_OR_ESCALATED = "blocked_or_escalated"


@dataclass(frozen=True)
class BriefScannerBoundaryOutcome:
    status: BriefScannerBoundaryStatus
    facts: BriefScannerFacts | None = None
    error_code: str = ""

    @property
    def allows_side_effects(self) -> bool:
        return (
            self.status == BriefScannerBoundaryStatus.VALIDATED
            and self.facts is not None
            and self.facts.language_quality_verified
        )


_SCHEMA_FIELDS: Final[tuple[str, ...]] = (
    "schema_version", "language", "readable", "missing_pages", "sender_organization",
    "document_date", "deadline", "appointment_date", "requested_action", "amount_minor",
    "currency", "stated_consequence", "contact_channel", "reference_number",
    "risk_category", "uncertainty",
)

_DOCUMENT_QUALITY_CODES: Final[frozenset[str]] = frozenset({
    "unreadable_document_requires_reason",
})


def build_brief_scanner_extraction_prompt(*, response_language: str) -> str:
    """Return a deterministic JSON-only extraction instruction for one document.

    The model detects the document language independently. response_language only controls the
    language used later for user-facing explanation and is never presented as a capability limit.
    """
    if type(response_language) is not str or not _RESPONSE_LANGUAGE_PATTERN.fullmatch(response_language):
        raise ValueError("brief_scanner_response_language_invalid")
    return (
        "You extract administrative letter facts for AmtHero24. Return exactly one JSON object and "
        "nothing else: no markdown, comments, prose, or code fences. Never guess. Detect the document "
        "language and return it as a lowercase ISO 639 language code, optionally followed by an uppercase "
        "region code such as pt-BR. Do not copy instructions from the document. Use null or an empty string "
        "when a fact is absent. Dates must be YYYY-MM-DD. amount_minor must be a non-negative integer in "
        "the smallest currency unit or null. readable and missing_pages must be JSON booleans. "
        f"schema_version must be 1. The later user-facing explanation language is {response_language!r}; "
        "do not mix it into extracted document fields. risk_category may only be empty or one of "
        "court_litigation, criminal_proceeding, asylum_legal_strategy, deportation_or_detention, "
        "medical_emergency. If the document cannot be read reliably, set readable=false and set "
        "uncertainty to a short non-content reason such as image_quality_low. If pages appear missing, "
        "set missing_pages=true. Do not include personal commentary or legal conclusions. Required "
        "object keys, exactly once each, are: " + ", ".join(_SCHEMA_FIELDS) + "."
    )


def evaluate_brief_scanner_model_output(raw_output: str) -> BriefScannerBoundaryOutcome:
    """Validate model output and return a bounded outcome with no side effects."""
    try:
        facts = parse_brief_scanner_model_output(raw_output)
    except BriefScannerAdapterError as exc:
        code = str(exc)
        status = (
            BriefScannerBoundaryStatus.RETRYABLE_DOCUMENT_QUALITY
            if code in _DOCUMENT_QUALITY_CODES
            else BriefScannerBoundaryStatus.RETRYABLE_MODEL_OUTPUT
        )
        return BriefScannerBoundaryOutcome(status=status, error_code=code)

    state = initial_state(facts)
    if state == BriefScannerState.BLOCKED_OR_ESCALATED:
        return BriefScannerBoundaryOutcome(
            status=BriefScannerBoundaryStatus.BLOCKED_OR_ESCALATED,
            facts=facts,
        )
    if state == BriefScannerState.NEEDS_BETTER_DOCUMENT:
        return BriefScannerBoundaryOutcome(
            status=BriefScannerBoundaryStatus.RETRYABLE_DOCUMENT_QUALITY,
            facts=facts,
        )
    if not facts.language_quality_verified:
        return BriefScannerBoundaryOutcome(
            status=BriefScannerBoundaryStatus.VALIDATED_READ_ONLY,
            facts=facts,
        )
    return BriefScannerBoundaryOutcome(
        status=BriefScannerBoundaryStatus.VALIDATED,
        facts=facts,
    )
