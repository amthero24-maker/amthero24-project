"""Structured document intelligence tests."""
from __future__ import annotations

from datetime import date

from document_intelligence import analyze_document_text, prompt_facts


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
