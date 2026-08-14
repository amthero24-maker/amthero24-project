"""Regression tests for the shared copy-safe official-draft boundary."""
from __future__ import annotations

import pytest

from official_draft_delivery import (
    DRAFT_MARKER,
    DRAFT_OUTPUT_KIND,
    END_MARKER,
    EXPLANATION_MARKER,
    build_copy_safe_prompt_contract,
    is_official_draft_turn,
    parse_copy_safe_draft_reply,
)


@pytest.mark.parametrize(
    "request",
    (
        "اكتبلي رسالة رسمية بالألماني إلى شركة التأمين.",
        "Kündige meinen Fitnessstudio-Vertrag zum nächstmöglichen Zeitpunkt.",
        "Write a refund request email to the seller.",
        "Підготуй лист для підтвердження мого терміну.",
        "Γράψε απάντηση για τον έλεγχο του συμβολαίου.",
    ),
)
def test_detects_official_draft_requests_across_languages_and_mvp_journeys(request: str) -> None:
    assert is_official_draft_turn(request, {}) is True


def test_document_explanation_is_not_misclassified_as_a_draft() -> None:
    assert is_official_draft_turn("اشرحلي رسالة الإلغاء بالعربي.", {}) is False
    assert is_official_draft_turn("Explain what this cancellation letter means.", {}) is False


def test_bounded_revision_requires_previous_draft_state() -> None:
    request = "عدّل المسودة وخلي تاريخ العقد صحيح."
    assert is_official_draft_turn(request, {}) is False
    assert is_official_draft_turn(
        request,
        {"session_output_kind": DRAFT_OUTPUT_KIND},
    ) is True
    assert is_official_draft_turn(
        "بالألماني",
        {"session_output_kind": DRAFT_OUTPUT_KIND},
    ) is True


def test_active_prompt_contract_requires_private_routing_markers() -> None:
    contract = build_copy_safe_prompt_contract(active=True, reply_language="Arabic")
    assert DRAFT_MARKER in contract
    assert EXPLANATION_MARKER in contract
    assert END_MARKER in contract
    assert "Never merge the explanation into the draft block" in contract
    assert build_copy_safe_prompt_contract(active=False, reply_language="Arabic") == ""


def test_marker_envelope_produces_two_clean_payloads() -> None:
    value = f"""{DRAFT_MARKER}
*Entwurf – Kündigung*

Betreff: Kündigung des Vertrags TEST-K-731

Sehr geehrte Damen und Herren,

hiermit kündige ich meinen Vertrag zum nächstmöglichen Zeitpunkt.

Mit freundlichen Grüßen
[Ihr Name]
{EXPLANATION_MARKER}
المسودة جاهزة. لم يتم إرسالها.
{END_MARKER}"""

    parsed = parse_copy_safe_draft_reply(value, conversation_language="ar")
    assert parsed is not None
    assert parsed.draft.startswith("Betreff: Kündigung")
    assert "Entwurf" not in parsed.draft
    assert "المسودة" not in parsed.draft
    assert parsed.explanation == "المسودة جاهزة. لم يتم إرسالها."


def test_current_legacy_cancellation_shape_is_split_without_mixing() -> None:
    value = """*Entwurf – Kündigung des Fitnessstudio-Vertrags*

MusterFit GmbH
Kundenservice
[Adresse von MusterFit GmbH]

Betreff: Kündigung des Vertrags Nr. TEST-K-731

Sehr geehrte Damen und Herren,

hiermit kündige ich meinen Mitgliedsvertrag zum nächstmöglichen Zeitpunkt.

Bitte bestätigen Sie mir schriftlich das Vertragsende.

Mit freundlichen Grüßen
[Ihr Vor- und Nachname]

*ما يعنيه النص:*
• الرسالة تطلب إلغاء الاشتراك بأقرب تاريخ ممكن.
• تُرسل كمسودة فقط ولم تُرسل بعد.

*الخطوة التالية:*
راجع بياناتك ثم أرسل الرسالة بنفسك."""

    parsed = parse_copy_safe_draft_reply(value, conversation_language="ar")
    assert parsed is not None
    assert parsed.draft.startswith("MusterFit GmbH")
    assert "ما يعنيه النص" not in parsed.draft
    assert "الخطوة التالية" not in parsed.draft
    assert "الرسالة تطلب إلغاء الاشتراك" in parsed.explanation


def test_pure_formal_draft_gets_a_localized_secondary_explanation() -> None:
    value = """Betreff: Rückfrage

Sehr geehrte Damen und Herren,

bitte bestätigen Sie mir den Termin schriftlich.

Mit freundlichen Grüßen
[Ihr Name]"""
    parsed = parse_copy_safe_draft_reply(value, conversation_language="ar")
    assert parsed is not None
    assert parsed.draft == value
    assert "الرسالة السابقة" in parsed.explanation
    assert "لم يتم إرسالها" in parsed.explanation


def test_partial_or_ambiguous_envelope_fails_closed() -> None:
    assert parse_copy_safe_draft_reply(
        f"{DRAFT_MARKER}\nNur ein unvollständiger Block",
        conversation_language="de",
    ) is None
    assert parse_copy_safe_draft_reply(
        "هذه إجابة عادية وليست مسودة رسمية.",
        conversation_language="ar",
    ) is None
