"""Pure tests for grounded refund, appointment, and contract follow-up drafts."""
from __future__ import annotations

import pytest

from journey_draft_grounding import (
    classify_journey_draft,
    classify_journey_request,
    ground_journey_draft,
)
from journey_grounding_explanations import (
    build_journey_companion_summary,
    build_journey_plain_explanation,
)
from journey_grounding_patterns import (
    JOURNEY_APPOINTMENT,
    JOURNEY_CONTRACT,
    JOURNEY_REFUND,
)

_REFUND_DRAFT = """MusterShop GmbH
[Adresse]

Betreff: Bitte um Prüfung einer Rückerstattung – Bestellung TEST-R-218

Sehr geehrte Damen und Herren,

am 02.08.2026 habe ich eine Bestellung über 79,90 EUR aufgegeben. Das Produkt ist nicht angekommen. Bitte prüfen Sie die Rückerstattung des gezahlten Betrags.

Mit freundlichen Grüßen
[Ihr Name]"""

_APPOINTMENT_DRAFT = """Bürgeramt Köln
Ottoplatz 1

Betreff: Bitte um Terminverschiebung – TEST-T-441

Sehr geehrte Damen und Herren,

ich bitte darum, meinen Termin am 20.08.2026 um 10:30 Uhr auf den 25.08.2026 um 14:00 Uhr zu verschieben. Ort: Ottoplatz 1. Bitte bestätigen Sie mir die Änderung schriftlich.

Mit freundlichen Grüßen
[Ihr Name]"""

_CONTRACT_DRAFT = """MusterNet GmbH
[Adresse]

Betreff: Rückfrage zum Vertrag TEST-V-882

Sehr geehrte Damen und Herren,

zu meinem Vertrag vom 01.06.2026 mit einem monatlichen Betrag von 39,90 EUR bitte ich um schriftliche Erläuterung der Verlängerungsklausel. Bitte teilen Sie mir mit, welche Verlängerungsdauer in meinem Vertrag vereinbart ist.

Mit freundlichen Grüßen
[Ihr Name]"""

_REQUESTS = {
    JOURNEY_REFUND: {
        "de": """Schreibe eine Rückerstattungsanfrage.
Anbieter: MusterShop GmbH
Betrag: 79,90 EUR
Kaufdatum: 02.08.2026
Bestellnummer: TEST-R-218
Das Produkt ist nicht angekommen.""",
        "ar": """اكتب رسالة طلب استرداد.
المزوّد: MusterShop GmbH
المبلغ: 79,90 EUR
تاريخ الشراء: 02.08.2026
رقم الطلب: TEST-R-218
المنتج لم يصل.""",
        "en": """Write a refund request.
Provider: MusterShop GmbH
Amount: 79,90 EUR
Transaction date: 02.08.2026
Reference: TEST-R-218
The product was not delivered.""",
        "uk": """Напиши лист про повернення коштів.
Постачальник: MusterShop GmbH
Сума: 79,90 EUR
Дата: 02.08.2026
Номер замовлення: TEST-R-218
Товар не доставлено.""",
        "el": """Γράψε επιστολή για επιστροφή χρημάτων.
Πάροχος: MusterShop GmbH
Ποσό: 79,90 EUR
Ημερομηνία: 02.08.2026
Αριθμός παραγγελίας: TEST-R-218
Το προϊόν δεν παραδόθηκε.""",
    },
    JOURNEY_APPOINTMENT: {
        "de": """Schreibe eine Nachricht zur Terminverschiebung.
Organisator: Bürgeramt Köln
Referenz: TEST-T-441
Bisheriger Termin: 20.08.2026 10:30
Neuer Wunschtermin: 25.08.2026 14:00
Ort: Ottoplatz 1""",
        "ar": """اكتب رسالة لتأجيل الموعد.
المنظم: Bürgeramt Köln
المرجع: TEST-T-441
الموعد: 20.08.2026 10:30
الموعد المطلوب: 25.08.2026 14:00
المكان: Ottoplatz 1""",
        "en": """Write an appointment rescheduling request.
Organizer: Bürgeramt Köln
Reference: TEST-T-441
Current appointment: 20.08.2026 10:30
Requested appointment: 25.08.2026 14:00
Location: Ottoplatz 1""",
        "uk": """Напиши лист про перенесення зустрічі.
Організатор: Bürgeramt Köln
Посилання: TEST-T-441
Дата: 20.08.2026 10:30
Дата: 25.08.2026 14:00
Місце: Ottoplatz 1""",
        "el": """Γράψε επιστολή για μεταφορά ραντεβού.
Διοργανωτής: Bürgeramt Köln
Αναφορά: TEST-T-441
Ημερομηνία: 20.08.2026 10:30
Ημερομηνία: 25.08.2026 14:00
Τοποθεσία: Ottoplatz 1""",
    },
    JOURNEY_CONTRACT: {
        "de": """Schreibe eine Rückfrage zum Vertrag.
Vertragspartner: MusterNet GmbH
Vertragsnummer: TEST-V-882
Betrag: 39,90 EUR
Vertragsdatum: 01.06.2026
Die Verlängerungsdauer ist nicht bekannt; bitte um schriftliche Klärung.""",
        "ar": """اكتب رسالة استفسار عن العقد.
الطرف: MusterNet GmbH
رقم العقد: TEST-V-882
المبلغ: 39,90 EUR
التاريخ: 01.06.2026
لا أعرف مدة التجديد وأريد توضيحًا كتابيًا.""",
        "en": """Write a clarification request about the contract.
Party: MusterNet GmbH
Reference: TEST-V-882
Amount: 39,90 EUR
Contract date: 01.06.2026
The renewal period is unknown; request written clarification.""",
        "uk": """Напиши лист з уточненням щодо договору.
Сторона: MusterNet GmbH
Номер договору: TEST-V-882
Сума: 39,90 EUR
Дата: 01.06.2026
Строк продовження невідомий; попроси письмове пояснення.""",
        "el": """Γράψε αίτημα διευκρίνισης για τη σύμβαση.
Μέρος: MusterNet GmbH
Αριθμός σύμβασης: TEST-V-882
Ποσό: 39,90 EUR
Ημερομηνία: 01.06.2026
Η διάρκεια ανανέωσης είναι άγνωστη· ζήτησε γραπτή διευκρίνιση.""",
    },
}

_DRAFTS = {
    JOURNEY_REFUND: _REFUND_DRAFT,
    JOURNEY_APPOINTMENT: _APPOINTMENT_DRAFT,
    JOURNEY_CONTRACT: _CONTRACT_DRAFT,
}

@pytest.mark.parametrize("journey", [JOURNEY_REFUND, JOURNEY_APPOINTMENT, JOURNEY_CONTRACT])
@pytest.mark.parametrize("language", ["de", "ar", "en", "uk", "el"])
def test_multilingual_requests_preserve_exact_grounded_anchors(journey: str, language: str) -> None:
    request = _REQUESTS[journey][language]
    draft = _DRAFTS[journey]

    assert classify_journey_request(request) == journey
    assert classify_journey_draft(draft) == journey
    result = ground_journey_draft(
        request,
        draft,
        conversation_language=language,
    )

    assert result.applicable is True
    assert result.journey == journey
    assert result.rejection_reason == ""
    assert result.draft == draft


@pytest.mark.parametrize(
    ("language", "heading"),
    [
        ("de", "Einfache Erklärung des Inhalts:"),
        ("ar", "شرح مبسّط للمحتوى:"),
        ("en", "Plain-language explanation:"),
        ("uk", "Просте пояснення змісту:"),
        ("el", "Απλή εξήγηση του περιεχομένου:"),
    ],
)
def test_localized_explanations_are_deterministic_and_read_only(language: str, heading: str) -> None:
    for draft in _DRAFTS.values():
        summary = build_journey_companion_summary(
            draft,
            conversation_language=language,
        )
        explanation = build_journey_plain_explanation(
            draft,
            conversation_language=language,
        )
        assert summary is not None
        assert explanation is not None
        assert explanation.startswith(heading)
        assert "TEST-" in explanation
        assert "[Adresse]" not in explanation
        assert "[Ihr Name]" not in explanation


def test_refund_rejects_changed_facts_missing_problem_and_outcome_claims() -> None:
    request = _REQUESTS[JOURNEY_REFUND]["ar"]

    changed_amount = ground_journey_draft(
        request,
        _REFUND_DRAFT.replace("79,90 EUR", "99,90 EUR"),
        conversation_language="ar",
    )
    assert changed_amount.rejection_reason == "unsupported-amount-added"

    missing_problem = ground_journey_draft(
        request,
        _REFUND_DRAFT.replace("Das Produkt ist nicht angekommen. ", ""),
        conversation_language="ar",
    )
    assert missing_problem.rejection_reason == "verified-problem-missing"

    changed_problem = ground_journey_draft(
        request,
        _REFUND_DRAFT.replace(
            "Das Produkt ist nicht angekommen.",
            "Der Betrag wurde doppelt abgebucht.",
        ),
        conversation_language="ar",
    )
    assert changed_problem.rejection_reason == "unsupported-problem-added"

    success_claim = ground_journey_draft(
        request,
        _REFUND_DRAFT.replace(
            "Bitte prüfen Sie die Rückerstattung des gezahlten Betrags.",
            "Die Rückerstattung wurde genehmigt und wird ausgezahlt.",
        ),
        conversation_language="ar",
    )
    assert success_claim.rejection_reason == "refund-success"


def test_appointment_rejects_new_schedule_location_and_completed_claim() -> None:
    request = _REQUESTS[JOURNEY_APPOINTMENT]["ar"]

    new_time = ground_journey_draft(
        request,
        _APPOINTMENT_DRAFT.replace("14:00 Uhr", "15:00 Uhr"),
        conversation_language="ar",
    )
    assert new_time.rejection_reason == "unsupported-time-added"

    new_location = ground_journey_draft(
        request,
        _APPOINTMENT_DRAFT.replace("Ottoplatz 1", "Neustraße 5"),
        conversation_language="ar",
    )
    assert new_location.rejection_reason == "unsupported-address-added"

    completed = ground_journey_draft(
        request,
        _APPOINTMENT_DRAFT.replace(
            "Bitte bestätigen Sie mir die Änderung schriftlich.",
            "Der Termin wurde bereits verschoben und bestätigt.",
        ),
        conversation_language="ar",
    )
    assert completed.rejection_reason == "appointment-completed"


def test_contract_rejects_legal_assertions_and_keeps_unknown_term_visible() -> None:
    request = _REQUESTS[JOURNEY_CONTRACT]["ar"]

    legal_assertion = ground_journey_draft(
        request,
        _CONTRACT_DRAFT.replace(
            "Bitte teilen Sie mir mit, welche Verlängerungsdauer in meinem Vertrag vereinbart ist.",
            "Die Klausel ist rechtswirksam und bindend.",
        ),
        conversation_language="ar",
    )
    assert legal_assertion.rejection_reason == "contract-validity"

    hidden_uncertainty = ground_journey_draft(
        request,
        _CONTRACT_DRAFT.replace(
            "bitte ich um schriftliche Erläuterung der Verlängerungsklausel. Bitte teilen Sie mir mit, welche Verlängerungsdauer in meinem Vertrag vereinbart ist.",
            "beziehe ich mich auf die Verlängerungsklausel.",
        ),
        conversation_language="ar",
    )
    assert hidden_uncertainty.rejection_reason == "contract-uncertainty-not-visible"

    invented_duration = ground_journey_draft(
        request,
        _CONTRACT_DRAFT.replace(
            "Bitte teilen Sie mir mit, welche Verlängerungsdauer in meinem Vertrag vereinbart ist.",
            "Die Verlängerungsdauer beträgt 12 Monate.",
        ),
        conversation_language="ar",
    )
    assert invented_duration.rejection_reason in {
        "unsupported-duration-added",
        "contract-term-assertion",
    }


def test_source_grounded_contract_term_and_explicit_refund_revision_are_allowed() -> None:
    grounded_contract_request = """Schreibe eine Rückfrage zum Vertrag.
Vertragspartner: MusterNet GmbH
Vertragsnummer: TEST-V-882
Betrag: 39,90 EUR
Vertragsdatum: 01.06.2026
Im Vertrag steht: Die Kündigungsfrist beträgt 3 Monate. Bitte um schriftliche Bestätigung."""
    grounded_contract_draft = _CONTRACT_DRAFT.replace(
        "bitte ich um schriftliche Erläuterung der Verlängerungsklausel. Bitte teilen Sie mir mit, welche Verlängerungsdauer in meinem Vertrag vereinbart ist.",
        "bitte ich um schriftliche Bestätigung, dass die Kündigungsfrist 3 Monate beträgt.",
    )
    result = ground_journey_draft(
        grounded_contract_request,
        grounded_contract_draft,
        conversation_language="de",
    )
    assert result.rejection_reason == ""

    revised = ground_journey_draft(
        "غيّر المبلغ في المسودة إلى 69,90 EUR.",
        _REFUND_DRAFT.replace("79,90 EUR", "69,90 EUR"),
        previous_draft=_REFUND_DRAFT,
        conversation_language="ar",
    )
    assert revised.rejection_reason == ""


def test_cancellation_and_generic_writing_remain_outside_layer() -> None:
    cancellation = "Schreibe eine Kündigung für meinen Vertrag bei MusterFit GmbH."
    generic = "Schreibe eine kurze allgemeine Anfrage an die Schule."

    assert classify_journey_request(cancellation) is None
    assert classify_journey_request(generic) is None
    assert classify_journey_draft(
        """Betreff: Kündigung

Sehr geehrte Damen und Herren,

hiermit kündige ich meinen Vertrag.

Mit freundlichen Grüßen
[Name]"""
    ) is None
