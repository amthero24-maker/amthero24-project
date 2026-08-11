"""Synthetic composition evidence for the six launch MVP journeys.

These tests deliberately stop at the provider and outbound boundaries. They prove
that the final reminder/language conversation composition used by production
reaches the shared safety prompt and reviewable reply path without calling Groq,
Meta, Railway, or any real user.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

import application
import reminder_extensions as reminder_base
import reminder_language_extensions as production
from data_store import JsonDataStore

production.install()


_TEXT_JOURNEYS = (
    (
        "writing",
        "Bitte entwirf eine E-Mail an die Hausverwaltung: Ich bitte um Bestätigung des Wartungstermins.",
        "OFFICIAL LETTERS & EMAILS",
    ),
    (
        "cancellation",
        "Bitte entwirf eine Kündigung an SyntheticTel für meinen Mobilfunkvertrag. Vertragsnummer SYN-1001.",
        "KÜNDIGUNG / CANCELLATION",
    ),
    (
        "contract-check",
        "Erkläre diese Vertragsklausel: Laufzeit zwölf Monate, danach Verlängerung um jeweils einen Monat.",
        "VERTRAGS-CHECK / CONTRACT CHECK",
    ),
    (
        "refund",
        "Bitte entwirf eine sachliche Rückerstattungsanfrage an SyntheticShop über 19,90 EUR für eine doppelte Abbuchung.",
        "GELD ZURÜCK / REFUND",
    ),
    (
        "appointment",
        "Mein Termin beim Bürgeramt ist am 20.08.2026 um 10:00 Uhr in Aachen. Erstelle eine Vorbereitungsliste.",
        "TERMIN ASSISTANCE",
    ),
)


def _seed_user(store: JsonDataStore, sender: str) -> None:
    store.update_user(sender, {
        "memory_consent": "granted",
        "memory_consent_at": datetime.now(UTC).isoformat(),
        "memory_consent_version": "synthetic-audit-v1",
        "onboarding_stage": "complete",
        "intro_sent_at": datetime.now(UTC).isoformat(),
        "preferred_language": "de",
    })


def _install_synthetic_store(tmp_path, name: str) -> tuple[JsonDataStore, str]:
    sender = "491000000000"
    store = JsonDataStore(tmp_path / f"{name}.json")
    production.core.store = store
    application.store = store
    application.core.store = store
    application.core._hero_memory_store = application.core.HeroMemory(store)
    reminder_base._REMINDER_REPOSITORY = None
    _seed_user(store, sender)
    return store, sender


def _provider_capture(target: dict[str, object]):
    def fake_generate_reply(*, system_prompt: str, user_text: str, image_bytes, mime_type: str) -> str:
        target.update({
            "system_prompt": system_prompt,
            "user_text": user_text,
            "image_bytes": image_bytes,
            "mime_type": mime_type,
        })
        return "SYNTHETIC REVIEWABLE RESULT — no external action executed."

    return fake_generate_reply


@pytest.mark.anyio
@pytest.mark.parametrize(("journey", "user_text", "marker"), _TEXT_JOURNEYS)
async def test_text_mvp_journey_reaches_final_production_prompt_and_reply_path(
    tmp_path,
    monkeypatch,
    journey: str,
    user_text: str,
    marker: str,
) -> None:
    for flag in (
        "BRIEF_SCANNER_RUNTIME_ENABLED",
        "BRIEF_SCANNER_RUNTIME_MISSION_ENABLED",
        "BRIEF_SCANNER_RUNTIME_DRAFT_ENABLED",
        "BRIEF_SCANNER_RUNTIME_REMINDER_ENABLED",
        "CLOSED_BETA_ADMISSION_ENABLED",
        "HUMAN_SUPPORT_ENABLED",
        "ENTITLEMENT_ENFORCEMENT_ENABLED",
        "REMINDER_WORKER_ENABLED",
    ):
        monkeypatch.setenv(flag, "false")

    store, sender = _install_synthetic_store(tmp_path, journey)
    message_id = f"synthetic-{journey}"
    message = production.core.IncomingMessage(message_id, sender, user_text, "text")
    assert store.claim_message(message_id, sender, user_text)
    assert production.core.process_incoming is production.process_incoming

    captured: dict[str, object] = {}
    with patch.object(
        production.core,
        "generate_reply",
        side_effect=_provider_capture(captured),
    ), patch.object(
        production.core,
        "send_whatsapp_message",
        new=AsyncMock(),
    ) as send:
        await production.process_incoming(message)

    prompt = str(captured["system_prompt"])
    assert captured["user_text"] == user_text
    assert captured["image_bytes"] is None
    assert "SIX MVP JOURNEY RUNTIME CONTRACT" in prompt
    assert marker in prompt
    assert "External execution is always a separate explicit boundary" in prompt
    send.assert_awaited_once_with(sender, "SYNTHETIC REVIEWABLE RESULT — no external action executed.")
    assert store.snapshot()["messages"][message_id]["status"] == "sent"
    if journey == "appointment":
        assert production.core._hero_memory().list_missions(sender, status="all", limit=5) == []


@pytest.mark.anyio
async def test_brief_scanner_pdf_reaches_final_prompt_without_persisting_extracted_content(
    tmp_path,
    monkeypatch,
) -> None:
    for flag in (
        "BRIEF_SCANNER_RUNTIME_ENABLED",
        "BRIEF_SCANNER_RUNTIME_MISSION_ENABLED",
        "BRIEF_SCANNER_RUNTIME_DRAFT_ENABLED",
        "BRIEF_SCANNER_RUNTIME_REMINDER_ENABLED",
        "CLOSED_BETA_ADMISSION_ENABLED",
        "REMINDER_WORKER_ENABLED",
    ):
        monkeypatch.setenv(flag, "false")

    store, sender = _install_synthetic_store(tmp_path, "brief-scanner")
    message_id = "synthetic-brief-scanner"
    original = production.core.IncomingMessage(
        message_id,
        sender,
        "Bitte erklären",
        "document",
        "synthetic-media-id",
        "application/pdf",
    )
    assert store.claim_message(
        message_id,
        sender,
        original.text,
        message_type=original.message_type,
        media_id=original.media_id,
    )

    extracted_text = (
        "Synthetic document facts: sender=Synthetic Amt; reference=SYN-2026-44; "
        "deadline=20.08.2026; requested action=submit a copy."
    )
    normalized = production.core.IncomingMessage(
        message_id,
        sender,
        extracted_text,
        "text",
        internal_context="document_analysis",
    )
    captured: dict[str, object] = {}

    with patch.object(
        application,
        "_extract_pdf_message",
        new=AsyncMock(return_value=normalized),
    ), patch.object(
        production.core,
        "generate_reply",
        side_effect=_provider_capture(captured),
    ), patch.object(
        production.core,
        "send_whatsapp_message",
        new=AsyncMock(),
    ) as send:
        await production.process_incoming(original)

    prompt = str(captured["system_prompt"])
    assert captured["user_text"] == extracted_text
    assert "SIX MVP JOURNEY RUNTIME CONTRACT" in prompt
    assert "BRIEF SCANNER" in prompt
    assert "Explain only facts supported by the supplied document/image" in prompt
    send.assert_awaited_once_with(sender, "SYNTHETIC REVIEWABLE RESULT — no external action executed.")

    profile = store.get_user(sender)
    assert profile["last_message"] == "PDF document processed transiently"
    assert "Synthetic Amt" not in str(profile.get("last_message") or "")
    assert "Synthetic Amt" not in str(profile.get("conversation_summary") or "")
    assert production.core._hero_memory().list_missions(sender, status="all", limit=5) == []
    assert store.snapshot()["messages"][message_id]["status"] == "sent"
