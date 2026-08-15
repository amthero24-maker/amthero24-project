"""Refined public policy for selected official-draft journey grounding.

The base grounder owns anchor extraction and semantic validation. This module narrows
activation to the exact journeys required by Issue #304 and adds outcome assertions
that must not be introduced by generated drafts. It is read-only and has no provider,
persistence, WhatsApp, payment, booking, or contract-action access.
"""
from __future__ import annotations

import re

import journey_draft_grounding as _base
from journey_grounding_patterns import (
    JOURNEY_APPOINTMENT,
    JOURNEY_CONTRACT,
    JOURNEY_REFUND,
    _APPOINTMENT_PATTERNS,
    _CANCELLATION_ACTION_PATTERNS,
    _CONTRACT_CLARIFICATION_PATTERN,
    _CONTRACT_FOLLOWUP_PATTERNS,
)

JourneyDraftGroundingResult = _base.JourneyDraftGroundingResult

_REFUND_EXTERNAL_ACTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "refund-request-sent",
        re.compile(
            r"(?:"
            r"(?:rückerstattungsantrag|erstattungsantrag|refund\s+request|reimbursement\s+request|"
            r"طلب\s+الاسترداد|طلب\s+التعويض|запит\s+на\s+повернення|"
            r"αίτημα\s+επιστροφής|αιτημα\s+επιστροφης)"
            r".{0,45}(?:wurde|ist|has\s+been|تم|було|έχει|εχει)"
            r".{0,35}(?:gesendet|verschickt|eingereicht|übermittelt|uebermittelt|"
            r"sent|submitted|filed|transmitted|إرسال|ارسال|تقديم|"
            r"надіслан|подан|απεστάλ|απεσταλ|υποβλήθ|υποβληθ)"
            r"|"
            r"(?:ich\s+habe|i\s+have|لقد|я\s+вже|έχω|εχω)"
            r".{0,45}(?:rückerstattung|erstattung|refund|reimbursement|استرداد|تعويض|"
            r"повернення|επιστροφή|επιστροφη)"
            r".{0,35}(?:beantragt|eingereicht|gesendet|requested|submitted|sent|"
            r"طلبت|قدمت|أرسلت|ارسلت|попросив|подал|надіслав|ζήτησα|ζητησα|υπέβαλα|υπεβαλα)"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        "refund-received",
        re.compile(
            r"(?:rückerstattung|erstattung|rückzahlung|refund|reimbursement|"
            r"استرداد|تعويض|повернення|επιστροφή|επιστροφη)"
            r".{0,45}(?:wurde|ist|has\s+been|تم|було|έχει|εχει)"
            r".{0,35}(?:eingegangen|erhalten|received|credited|وصل|استلام|"
            r"отриман|зарахован|λήφθ|ληφθ|πιστώθ|πιστωθ)",
            re.IGNORECASE,
        ),
    ),
)


def _contains_any(value: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    text = _base._normalize(value)  # noqa: SLF001 - same-package read-only helper
    return any(pattern.search(text) for pattern in patterns)


def _is_contract_cancellation(value: str) -> bool:
    """Exclude contract termination while keeping appointment cancellation in scope."""
    text = _base._normalize(value)  # noqa: SLF001 - same-package read-only helper
    return bool(
        _contains_any(text, _CANCELLATION_ACTION_PATTERNS)
        and not _contains_any(text, _APPOINTMENT_PATTERNS)
    )


def _is_contract_follow_up(value: str) -> bool:
    text = _base._normalize(value)  # noqa: SLF001 - same-package read-only helper
    return bool(
        _contains_any(text, _CONTRACT_FOLLOWUP_PATTERNS)
        or _CONTRACT_CLARIFICATION_PATTERN.search(text)
    )


def classify_target_journey_request(value: str) -> str | None:
    """Classify only refund, appointment correspondence, or contract follow-up."""
    if _is_contract_cancellation(value):
        return None
    journey = _base.classify_journey_request(value)
    if journey in {JOURNEY_REFUND, JOURNEY_APPOINTMENT}:
        return journey
    if journey == JOURNEY_CONTRACT and _is_contract_follow_up(value):
        return journey
    return None


def classify_target_journey_draft(value: str) -> str | None:
    """Classify only a copy-ready draft that matches the bounded target journey."""
    if _is_contract_cancellation(value):
        return None
    journey = _base.classify_journey_draft(value)
    if journey in {JOURNEY_REFUND, JOURNEY_APPOINTMENT}:
        return journey
    if journey == JOURNEY_CONTRACT and _is_contract_follow_up(value):
        return journey
    return None


def classify_target_journey(request_text: str, previous_draft: str = "") -> str | None:
    return (
        classify_target_journey_request(request_text)
        or classify_target_journey_draft(previous_draft)
    )


def _introduced_refund_external_action(source: str, draft: str) -> str:
    source_text = _base._normalize(source)  # noqa: SLF001 - same-package helper
    draft_text = _base._normalize(draft)  # noqa: SLF001 - same-package helper
    for reason, pattern in _REFUND_EXTERNAL_ACTION_PATTERNS:
        if pattern.search(draft_text) and not pattern.search(source_text):
            return reason
    return ""


def ground_target_journey_draft(
    request_text: str,
    draft: str,
    *,
    previous_draft: str = "",
    conversation_language: str = "de",
) -> JourneyDraftGroundingResult:
    """Apply the base fail-closed grounder under the narrower production policy."""
    clean = _base._normalize(draft)  # noqa: SLF001 - same-package helper
    journey = classify_target_journey(request_text, previous_draft)
    if journey is None:
        return JourneyDraftGroundingResult(
            applicable=False,
            journey="",
            draft=clean,
        )

    if classify_target_journey_draft(clean) != journey:
        return JourneyDraftGroundingResult(
            applicable=True,
            journey=journey,
            draft=clean,
            rejection_reason="journey-draft-mismatch",
        )

    grounded = _base.ground_journey_draft(
        request_text,
        clean,
        previous_draft=previous_draft,
        conversation_language=conversation_language,
    )
    if not grounded.applicable or grounded.journey != journey:
        return JourneyDraftGroundingResult(
            applicable=True,
            journey=journey,
            draft=clean,
            rejection_reason="journey-draft-mismatch",
        )
    if grounded.rejection_reason:
        return grounded

    if journey == JOURNEY_REFUND:
        source = "\n".join(
            part
            for part in (
                _base._normalize(request_text),  # noqa: SLF001
                _base._normalize(previous_draft),  # noqa: SLF001
            )
            if part
        )
        reason = _introduced_refund_external_action(source, grounded.draft)
        if reason:
            return JourneyDraftGroundingResult(
                applicable=True,
                journey=journey,
                draft=clean,
                rejection_reason=reason,
            )

    return grounded


# Stable public aliases for focused tests and future bounded composition.
classify_journey_request = classify_target_journey_request
classify_journey_draft = classify_target_journey_draft
classify_journey = classify_target_journey
ground_journey_draft = ground_target_journey_draft
