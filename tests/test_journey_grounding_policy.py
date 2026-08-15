"""Focused policy regressions for bounded journey-draft activation and outcomes."""
from __future__ import annotations

import pytest

from journey_grounding_patterns import (
    JOURNEY_APPOINTMENT,
    JOURNEY_CONTRACT,
    JOURNEY_REFUND,
)
from journey_grounding_policy import (
    classify_journey_draft,
    classify_journey_request,
    ground_journey_draft,
)

_REFUND_REQUEST = """اكتب رسالة طلب استرداد.
المزوّد: MusterShop GmbH
المبلغ: 79,90 EUR
تاريخ الشراء: 02.08.2026
رقم الطلب: TEST-R-218
المنتج لم يصل."""

_REFUND_DRAFT = """MusterShop GmbH
[Adresse]

Betreff: Bitte um Prüfung einer Rückerstattung – Bestellung TEST-R-218

Sehr geehrte Damen und Herren,

am 02.08.2026 habe ich eine Bestellung über 79,90 EUR aufgegeben. Das Produkt ist nicht angekommen. Bitte prüfen Sie die Rückerstattung des gezahlten Betrags.

Mit freundlichen Grüßen
[Ihr Name]"""

_CONTRACT_REQUESTS = {
    "de": "Schreibe eine Rückfrage zum Vertrag mit MusterNet GmbH und bitte um schriftliche Klärung der unbekannten Verlängerungsdauer.",
    "ar": "اكتب رسالة استفسار عن العقد مع MusterNet GmbH واطلب توضيحًا كتابيًا لمدة التجديد غير المعروفة.",
    "en": "Write a contract clarification request to MusterNet GmbH and ask for written confirmation of the unknown renewal period.",
    "uk": "Напиши лист з уточненням щодо договору з MusterNet GmbH і попроси письмове пояснення невідомого строку продовження.",
    "el": "Γράψε αίτημα διευκρίνισης για τη σύμβαση με MusterNet GmbH και ζήτησε γραπτή επιβεβαίωση της άγνωστης διάρκειας ανανέωσης.",
}

_CONTRACT_DRAFT = """MusterNet GmbH
[Adresse]

Betreff: Rückfrage zum Vertrag

Sehr geehrte Damen und Herren,

bitte erläutern Sie mir schriftlich, welche Verlängerungsdauer für meinen Vertrag vereinbart ist.

Mit freundlichen Grüßen
[Ihr Name]"""


@pytest.mark.parametrize("language", ["de", "ar", "en", "uk", "el"])
def test_contract_follow_up_is_supported_in_all_product_languages(language: str) -> None:
    assert classify_journey_request(_CONTRACT_REQUESTS[language]) == JOURNEY_CONTRACT
    assert classify_journey_draft(_CONTRACT_DRAFT) == JOURNEY_CONTRACT


def test_generic_contract_creation_and_contract_cancellation_remain_outside_layer() -> None:
    generic_contract = "Schreibe einen vollständigen neuen Vertrag zwischen zwei Personen."
    cancellation = (
        "Schreibe eine Kündigung für meinen Vertrag bei MusterNet GmbH und bitte um "
        "schriftliche Bestätigung."
    )
    generic_contract_draft = """Betreff: Neuer Vertrag

Sehr geehrte Damen und Herren,

anbei erhalten Sie den vollständigen Vertragsentwurf.

Mit freundlichen Grüßen
[Ihr Name]"""

    assert classify_journey_request(generic_contract) is None
    assert classify_journey_request(cancellation) is None
    assert classify_journey_draft(generic_contract_draft) is None


def test_appointment_cancellation_is_not_misrouted_as_contract_termination() -> None:
    request = "اكتب رسالة بالألماني لإلغاء موعدي عند Bürgeramt Köln، ولا ترسلها."
    draft = """Bürgeramt Köln
[Adresse]

Betreff: Absage meines Termins

Sehr geehrte Damen und Herren,

ich möchte meinen Termin absagen. Bitte bestätigen Sie mir den Eingang dieser Nachricht schriftlich.

Mit freundlichen Grüßen
[Ihr Name]"""

    assert classify_journey_request(request) == JOURNEY_APPOINTMENT
    assert classify_journey_draft(draft) == JOURNEY_APPOINTMENT


def test_refund_rejects_new_claim_that_request_was_sent_or_money_received() -> None:
    sent_claim = _REFUND_DRAFT.replace(
        "Bitte prüfen Sie die Rückerstattung des gezahlten Betrags.",
        "Der Rückerstattungsantrag wurde bereits eingereicht. Bitte prüfen Sie ihn.",
    )
    sent_result = ground_journey_draft(
        _REFUND_REQUEST,
        sent_claim,
        conversation_language="ar",
    )
    assert sent_result.applicable is True
    assert sent_result.journey == JOURNEY_REFUND
    assert sent_result.rejection_reason == "refund-request-sent"

    received_claim = _REFUND_DRAFT.replace(
        "Bitte prüfen Sie die Rückerstattung des gezahlten Betrags.",
        "Die Rückerstattung ist bereits eingegangen.",
    )
    received_result = ground_journey_draft(
        _REFUND_REQUEST,
        received_claim,
        conversation_language="ar",
    )
    assert received_result.rejection_reason == "refund-received"


def test_user_supplied_prior_refund_request_fact_can_be_stated_neutrally() -> None:
    request = _REFUND_REQUEST + "\nلقد أرسلت طلب الاسترداد سابقًا وأريد متابعة مكتوبة."
    follow_up = _REFUND_DRAFT.replace(
        "Bitte prüfen Sie die Rückerstattung des gezahlten Betrags.",
        "Ich habe die Rückerstattung bereits beantragt. Bitte teilen Sie mir den Bearbeitungsstand schriftlich mit.",
    )
    result = ground_journey_draft(
        request,
        follow_up,
        conversation_language="ar",
    )
    assert result.rejection_reason == ""
