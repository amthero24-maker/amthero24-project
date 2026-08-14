"""Structured document intelligence tests."""
from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

import pytest

import reminder_language_extensions as language_layer
from data_store import JsonDataStore
from document_intelligence import analyze_document_text, prompt_facts
from pasted_document_grounding import (
    extract_pasted_invoice_facts,
    grounded_pasted_invoice_reply,
)


_PASTED_INVOICE = """وصلتني هالرسالة، اشرحلي بالعربي شو المطلوب مني:

Musterstadt Energie GmbH
Datum: 14.08.2026
Betreff: Offene Rechnung
Kundennummer: TEST-4821
Betrag: 48,50 EUR
Zahlungsfrist: 28.08.2026

Bitte überweisen Sie den offenen Betrag bis zum genannten Datum.
Falls Sie bereits bezahlt haben, senden Sie uns bitte einen Zahlungsnachweis.
"""


def test_extracts_deadline_amount_reference_and_category() -> None:
    text = """
    Jobcenter Düsseldorf
    Aktenzeichen: JC/2026/123
    Bitte reichen Sie die Unterlagen bis zum 10.08.2026 ein.
    Offener Betrag: 123,45 EUR.
    """
    analysis = analyze_document_text(text, language="ar", source_kind="pdf", today=date(2026, 8, 5))

    assert analysis.category == "jobcenter"
    assert analysis.authority == "Jobcenter"
    assert analysis.deadline == "2026-08-10"
    assert analysis.amounts == ("123,45 EUR",)
    assert analysis.references[0].startswith("JC/2026/123")
    assert analysis.urgency == "high"
    assert analysis.actionable is True
    assert analysis.pending_action() == {
        "title": analysis.title,
        "topic": "jobcenter",
        "due_at": "2026-08-10",
        "next_step": analysis.next_step,
        "authority": "Jobcenter",
        "source_kind": "pdf",
    }


def test_document_date_without_deadline_context_is_not_treated_as_deadline() -> None:
    analysis = analyze_document_text(
        "Bescheid vom 06.03.2026. Dieses Schreiben bestätigt den Eingang Ihres Antrags.",
        language="de",
        today=date(2026, 3, 7),
    )
    assert analysis.deadline is None
    assert analysis.urgency == "normal"


def test_prompt_exposes_verified_hints_but_pending_action_excludes_sensitive_details() -> None:
    analysis = analyze_document_text(
        "Rechnung Nr. 20251478. Zahlbar bis zum 20.08.2026. Gesamtbetrag 89,90 EUR.",
        language="ar",
        today=date(2026, 8, 1),
    )
    prompt = prompt_facts(analysis, language="ar")
    pending = analysis.pending_action()

    assert "2026-08-20" in prompt
    assert "89,90 EUR" in prompt
    assert "نعم سجّلها" in prompt
    assert pending is not None
    assert "amounts" not in pending
    assert "references" not in pending


def test_pasted_invoice_grounding_keeps_customer_number_identifier_only() -> None:
    facts = extract_pasted_invoice_facts(_PASTED_INVOICE)
    assert facts is not None
    assert facts.sender == "Musterstadt Energie GmbH"
    assert facts.amount == "48,50 EUR"
    assert facts.deadline == "28.08.2026"
    assert facts.customer_number == "TEST-4821"
    assert facts.payment_purpose == ""
    assert facts.customer_number_explicitly_assigned_as_purpose is False

    reply = grounded_pasted_invoice_reply(_PASTED_INVOICE, language="ar")
    assert reply is not None
    assert "Musterstadt Energie GmbH" in reply
    assert "48,50 EUR" in reply
    assert "28.08.2026" in reply
    assert "TEST-4821 ظاهر كرقم عميل فقط" in reply
    assert "النص ما بيطلب استخدامه كمرجع أو غرض للتحويل" in reply
    assert "بيانات الحساب وغرض التحويل غير ظاهرين" in reply
    assert "اكتب رقم العميل" not in reply
    assert "في أقرب وقت" not in reply


def test_pasted_invoice_grounding_allows_only_explicit_payment_purpose() -> None:
    text = _PASTED_INVOICE.replace(
        "Betrag: 48,50 EUR",
        "Betrag: 48,50 EUR\nVerwendungszweck: RE-2026-48",
    )
    reply = grounded_pasted_invoice_reply(text, language="de")
    assert reply is not None
    assert "Angegebener Verwendungszweck: RE-2026-48" in reply
    assert "nur als Kundennummer bezeichnet" not in reply
    assert "Die Bankverbindung fehlt" in reply


def test_pasted_invoice_grounding_fails_closed_for_unstructured_chat() -> None:
    assert grounded_pasted_invoice_reply(
        "Ich brauche Hilfe mit einer Rechnung.",
        language="de",
    ) is None


@pytest.mark.anyio
async def test_final_language_layer_skips_model_for_grounded_pasted_invoice(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    store.update_user("49123", {
        "memory_consent": "granted",
        "memory_consent_at": datetime.now(UTC).isoformat(),
        "memory_consent_version": "test-v1",
        "onboarding_stage": "complete",
        "preferred_language": "ar",
        "session_language": "ar",
    })
    monkeypatch.setattr(language_layer.core, "store", store)

    message = language_layer.core.IncomingMessage(
        "invoice-text-1",
        "49123",
        _PASTED_INVOICE,
        "text",
    )
    store.claim_message(message.message_id, message.sender, message.text)

    blocked_delegate = AsyncMock(
        side_effect=AssertionError("grounded invoice must not reach model path")
    )
    with patch.object(
        language_layer,
        "_ORIGINAL_PROCESS_INCOMING",
        new=blocked_delegate,
    ), patch.object(
        language_layer.core,
        "send_whatsapp_message",
        new=AsyncMock(),
    ) as send:
        await language_layer.process_incoming(message)

    blocked_delegate.assert_not_awaited()
    send.assert_awaited_once()
    reply = send.await_args.args[1]
    assert "TEST-4821 ظاهر كرقم عميل فقط" in reply
    assert "اكتب رقم العميل" not in reply

    profile = store.get_user("49123")
    assert profile["session_topic"] == "document"
    assert profile["current_topic"] == "document"
    assert profile["last_message"] == "Pasted document text processed transiently"
    assert "Musterstadt Energie GmbH\nDatum" not in profile["conversation_summary"]
    assert store.snapshot()["messages"]["invoice-text-1"]["status"] == "sent"
