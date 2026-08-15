"""Deterministic tests for non-cancellation official-draft grounding."""
from __future__ import annotations

import pytest

from mvp_draft_grounding import (
    OfficialDraftJourney,
    build_grounding_failure_message,
    build_journey_companion_summary,
    build_journey_plain_explanation,
    classify_official_draft_journey,
    ground_official_journey_draft,
)

_REFUND_REQUEST = """اكتبلي طلب استرداد بالألماني ولا ترسله.
Anbieter: SyntheticShop GmbH
Bestellnummer: TEST-R-22
Betrag: 19,90 EUR
Datum: 01.08.2026
السبب: تم الخصم مرتين.
"""
_REFUND_DRAFT = """SyntheticShop GmbH

Betreff: Rückerstattung – Bestellnummer TEST-R-22

Sehr geehrte Damen und Herren,

zu meiner Bestellung TEST-R-22 wurde am 01.08.2026 ein Betrag von 19,90 EUR doppelt abgebucht. Ich bitte um Prüfung und Rückerstattung des doppelt berechneten Betrags.

Bitte bestätigen Sie den Eingang dieser Anfrage schriftlich.

Mit freundlichen Grüßen
[Ihr Name]"""

_APPOINTMENT_REQUEST = """اكتبلي رسالة لتأجيل الموعد بالألماني ولا ترسلها.
Organisator: Bürgeramt Aachen
Terminreferenz: TEST-T-9
Termin: 20.08.2026 um 10:00 Uhr
Ort: Musterstraße 10, Aachen
ما عندي موعد بديل؛ اطلب منهم اقتراح موعد جديد.
"""
_APPOINTMENT_DRAFT = """Bürgeramt Aachen
Musterstraße 10, Aachen

Betreff: Bitte um Verschiebung des Termins – TEST-T-9

Sehr geehrte Damen und Herren,

meinen Termin am 20.08.2026 um 10:00 Uhr kann ich leider nicht wahrnehmen. Bitte schlagen Sie mir einen neuen Termin vor.

Bitte bestätigen Sie mir die Änderung schriftlich.

Mit freundlichen Grüßen
[Ihr Name]"""

_CONTRACT_REQUEST = """اكتبلي رسالة استفسار بالألماني عن العقد ولا ترسلها.
Vertragspartner: MusterVertrag GmbH
Vertragsnummer: TEST-V-44
Betrag: 12,50 EUR
Datum: 01.09.2026
بدي توضيح مكتوب للبند المتعلق بالتجديد.
"""
_CONTRACT_DRAFT = """MusterVertrag GmbH

Betreff: Rückfrage zum Vertrag TEST-V-44

Sehr geehrte Damen und Herren,

bitte erläutern Sie mir schriftlich die Regelung zur Verlängerung meines Vertrags TEST-V-44 ab dem 01.09.2026 und wie sich der Betrag von 12,50 EUR zusammensetzt.

Mit freundlichen Grüßen
[Ihr Name]"""


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("Bitte formuliere eine Rückerstattungsanfrage.", OfficialDraftJourney.REFUND),
        ("اكتبلي طلب استرداد للمبلغ.", OfficialDraftJourney.REFUND),
        ("Write a refund request.", OfficialDraftJourney.REFUND),
        ("Підготуй запит на повернення коштів.", OfficialDraftJourney.REFUND),
        ("Γράψε αίτημα επιστροφής χρημάτων.", OfficialDraftJourney.REFUND),
        ("Bitte schreibe eine Nachricht zur Verschiebung meines Termins.", OfficialDraftJourney.APPOINTMENT),
        ("اكتب رسالة لتأجيل الموعد.", OfficialDraftJourney.APPOINTMENT),
        ("Write an email to reschedule my appointment.", OfficialDraftJourney.APPOINTMENT),
        ("اكتب رسالة استفسار عن بند في العقد.", OfficialDraftJourney.CONTRACT),
        ("Write a clarification email about a contract clause.", OfficialDraftJourney.CONTRACT),
    ),
)
def test_classifies_high_confidence_official_draft_journeys(
    text: str,
    expected: OfficialDraftJourney,
) -> None:
    assert classify_official_draft_journey(text) is expected


def test_cancellation_and_generic_writing_stay_outside_this_layer() -> None:
    assert classify_official_draft_journey("اكتبلي رسالة إلغاء عقد بأقرب موعد ممكن.") is None
    assert classify_official_draft_journey("Write a general thank-you email.") is None


def test_safe_refund_draft_preserves_all_verified_anchors() -> None:
    result = ground_official_journey_draft(
        _REFUND_REQUEST,
        _REFUND_DRAFT,
        conversation_language="ar",
    )
    assert result.applicable is True
    assert result.journey is OfficialDraftJourney.REFUND
    assert result.rejection_reason == ""


@pytest.mark.parametrize(
    ("draft", "reason"),
    (
        (_REFUND_DRAFT.replace("19,90 EUR", "29,90 EUR"), "unsupported-anchor-added"),
        (_REFUND_DRAFT.replace(" am 01.08.2026", ""), "verified-anchor-missing"),
        (
            _REFUND_DRAFT.replace(
                "Ich bitte um Prüfung",
                "Ich habe einen gesetzlichen Anspruch auf Erstattung. Ich bitte um Prüfung",
            ),
            "unsupported-journey-claim",
        ),
        (
            _REFUND_DRAFT.replace(
                "Bitte bestätigen Sie den Eingang",
                "Die Rückerstattung wurde genehmigt. Bitte bestätigen Sie den Eingang",
            ),
            "unsupported-journey-claim",
        ),
    ),
)
def test_refund_rejects_invented_values_and_outcomes(draft: str, reason: str) -> None:
    result = ground_official_journey_draft(
        _REFUND_REQUEST,
        draft,
        conversation_language="ar",
    )
    assert result.rejection_reason == reason


def test_safe_appointment_change_preserves_date_time_location_and_reference() -> None:
    result = ground_official_journey_draft(
        _APPOINTMENT_REQUEST,
        _APPOINTMENT_DRAFT,
        conversation_language="ar",
    )
    assert result.applicable is True
    assert result.journey is OfficialDraftJourney.APPOINTMENT
    assert result.rejection_reason == ""


@pytest.mark.parametrize(
    "draft",
    (
        _APPOINTMENT_DRAFT.replace("neuen Termin", "neuen Termin am 21.08.2026 um 11:00 Uhr"),
        _APPOINTMENT_DRAFT.replace("Bitte bestätigen Sie mir die Änderung schriftlich.", "Ihr Termin wurde verschoben."),
        _APPOINTMENT_DRAFT.replace("Musterstraße 10, Aachen\n\n", ""),
    ),
)
def test_appointment_rejects_invented_or_lost_operational_facts(draft: str) -> None:
    result = ground_official_journey_draft(
        _APPOINTMENT_REQUEST,
        draft,
        conversation_language="ar",
    )
    assert result.rejection_reason in {
        "unsupported-anchor-added",
        "unsupported-journey-claim",
        "verified-labelled-fact-missing",
    }


def test_safe_contract_follow_up_is_neutral_and_grounded() -> None:
    result = ground_official_journey_draft(
        _CONTRACT_REQUEST,
        _CONTRACT_DRAFT,
        conversation_language="ar",
    )
    assert result.applicable is True
    assert result.journey is OfficialDraftJourney.CONTRACT
    assert result.rejection_reason == ""


@pytest.mark.parametrize(
    "draft",
    (
        _CONTRACT_DRAFT.replace("12,50 EUR", "20,00 EUR"),
        _CONTRACT_DRAFT.replace("bitte erläutern", "Die Klausel ist unwirksam. Bitte erläutern"),
        _CONTRACT_DRAFT.replace("01.09.2026", "02.09.2026"),
    ),
)
def test_contract_follow_up_rejects_new_values_or_legal_conclusions(draft: str) -> None:
    result = ground_official_journey_draft(
        _CONTRACT_REQUEST,
        draft,
        conversation_language="ar",
    )
    assert result.rejection_reason in {"unsupported-anchor-added", "unsupported-journey-claim"}


def test_fixed_duration_requires_matching_source_evidence() -> None:
    request = _REFUND_REQUEST + "\nBitte um Antwort innerhalb von 14 Tagen."
    safe = _REFUND_DRAFT.replace(
        "Bitte bestätigen Sie den Eingang dieser Anfrage schriftlich.",
        "Bitte antworten Sie innerhalb von 14 Tagen schriftlich.",
    )
    assert ground_official_journey_draft(request, safe).rejection_reason == ""
    assert ground_official_journey_draft(
        request,
        safe.replace("14 Tagen", "30 Tagen"),
    ).rejection_reason == "unsupported-anchor-added"
    assert ground_official_journey_draft(
        _REFUND_REQUEST,
        safe,
    ).rejection_reason == "unsupported-anchor-added"


@pytest.mark.parametrize("language", ("de", "ar", "en", "uk", "el"))
def test_companion_and_plain_explanation_are_deterministic(language: str) -> None:
    summary = build_journey_companion_summary(
        _REFUND_DRAFT,
        journey=OfficialDraftJourney.REFUND,
        conversation_language=language,
    )
    explanation = build_journey_plain_explanation(
        _APPOINTMENT_DRAFT,
        journey=OfficialDraftJourney.APPOINTMENT,
        conversation_language=language,
    )
    assert summary is not None and "SyntheticShop GmbH" in summary
    assert explanation is not None and "Bürgeramt" in explanation
    assert "BAD MODEL" not in summary + explanation


def test_rejected_revision_can_use_previous_clean_draft_as_source_context() -> None:
    request = "عدّل المسودة وخليها أقصر."
    shortened = _REFUND_DRAFT.replace(
        "Bitte bestätigen Sie den Eingang dieser Anfrage schriftlich.\n\n",
        "",
    )
    result = ground_official_journey_draft(
        request,
        shortened,
        previous_draft=_REFUND_DRAFT,
        conversation_language="ar",
    )
    assert result.journey is OfficialDraftJourney.REFUND
    assert result.rejection_reason == ""


def test_failure_message_is_localized_and_never_claims_sending() -> None:
    message = build_grounding_failure_message(
        journey=OfficialDraftJourney.CONTRACT,
        conversation_language="ar",
    )
    assert "لم أرسل" in message
    assert "أوقفت المسودة" in message
