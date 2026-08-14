"""Regression tests for copy-safe, source-faithful pasted-document drafts."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from conversation_intelligence import detect_language
from data_store import JsonDataStore
from writing_grounding_extensions import (
    build_grounded_payment_information_draft,
    install,
)


_REQUEST = """اكتبلي رد بالألماني على هالرسالة، ولا ترسله. بدي أسألهم عن بيانات الحساب وغرض التحويل لأنهم غير ظاهرين بالنص:

Musterstadt Energie GmbH
Datum: 14.08.2026
Betreff: Offene Rechnung
Kundennummer: TEST-4821
Betrag: 48,50 EUR
Zahlungsfrist: 28.08.2026

Bitte überweisen Sie den offenen Betrag bis zum genannten Datum.
Falls Sie bereits bezahlt haben, senden Sie uns bitte einen Zahlungsnachweis.
"""


def test_grounded_draft_preserves_source_type_and_document_date_role() -> None:
    reply = build_grounded_payment_information_draft(
        _REQUEST,
        conversation_language="ar",
    )

    assert reply is not None
    draft = reply.draft
    assert "Ihr Schreiben vom 14.08.2026" in draft
    assert "Betreff „Offene Rechnung“" in draft
    assert "48,50 EUR" in draft
    assert "28.08.2026" in draft
    assert "Kundennummer TEST-4821" in draft
    assert "Bankverbindung" in draft
    assert "Verwendungszweck" in draft
    assert "Mahnung" not in draft
    assert "Rechnung vom 14.08.2026" not in draft
    assert "IBAN" not in draft
    assert "BIC" not in draft
    assert "Zahlungseingang" not in draft


def test_copyable_draft_and_user_explanation_are_separate_payloads() -> None:
    reply = build_grounded_payment_information_draft(
        _REQUEST,
        conversation_language="ar",
    )

    assert reply is not None
    assert "المسودة" not in reply.draft
    assert "الرسالة السابقة" not in reply.draft
    assert "لم يتم إرسالها" not in reply.draft
    assert "المسودة بالرسالة السابقة منفصلة" in reply.explanation
    assert "لم يتم إرسالها" in reply.explanation
    assert "Musterstadt Energie GmbH" not in reply.explanation


def _seed_complete_user(store: JsonDataStore) -> None:
    store.update_user("49123", {
        "memory_consent": "granted",
        "memory_consent_at": datetime.now(UTC).isoformat(),
        "memory_consent_version": "test-v1",
        "onboarding_stage": "complete",
        "preferred_language": "ar",
        "session_language": "ar",
    })


def _core(
    store: JsonDataStore,
    *,
    delegate: AsyncMock,
    send: AsyncMock,
) -> SimpleNamespace:
    core = SimpleNamespace(
        store=store,
        process_incoming=delegate,
        detect_language=detect_language,
        _session_expiry=lambda: "2026-08-16T00:00:00+00:00",
        _now=lambda: datetime(2026, 8, 15, tzinfo=UTC),
        send_whatsapp_message=send,
    )

    async def finish(message_id: str, reply: str, sender: str) -> None:
        await send(sender, reply)
        store.update_message_status(message_id, "sent")

    core._finish = finish
    return core


@pytest.mark.anyio
async def test_runtime_sends_the_german_draft_as_its_own_whatsapp_message(
    tmp_path,
) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _seed_complete_user(store)
    store.claim_message("writing-draft-1", "49123", _REQUEST)
    delegate = AsyncMock()
    send = AsyncMock()
    core = _core(store, delegate=delegate, send=send)
    install(core)

    message = SimpleNamespace(
        message_id="writing-draft-1",
        sender="49123",
        text=_REQUEST,
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    delegate.assert_not_awaited()
    assert send.await_count == 2
    draft = send.await_args_list[0].args[1]
    explanation = send.await_args_list[1].args[1]
    assert "Sehr geehrte Damen und Herren" in draft
    assert "Mahnung" not in draft
    assert "المسودة" not in draft
    assert explanation.startswith("المسودة بالرسالة السابقة منفصلة")

    profile = store.get_user("49123")
    assert profile["session_last_reply"] == draft
    assert profile["session_topic"] == "document"
    assert profile["current_topic"] == "document"
    assert profile["last_message"] == "Pasted document draft processed transiently"
    assert "last_assistant_reply" not in profile
    assert "Musterstadt Energie GmbH\nDatum" not in profile["conversation_summary"]
    assert store.snapshot()["messages"]["writing-draft-1"]["status"] == "sent"


@pytest.mark.anyio
async def test_secondary_explanation_failure_does_not_replay_or_fail_primary_draft(
    tmp_path,
) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _seed_complete_user(store)
    store.claim_message("writing-draft-2", "49123", _REQUEST)
    delegate = AsyncMock()
    send = AsyncMock(side_effect=[None, RuntimeError("secondary unavailable")])
    core = _core(store, delegate=delegate, send=send)
    install(core)

    message = SimpleNamespace(
        message_id="writing-draft-2",
        sender="49123",
        text=_REQUEST,
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    delegate.assert_not_awaited()
    assert send.await_count == 2
    assert "Sehr geehrte Damen und Herren" in send.await_args_list[0].args[1]
    assert store.snapshot()["messages"]["writing-draft-2"]["status"] == "sent"


@pytest.mark.anyio
async def test_unrelated_draft_request_stays_on_existing_writing_path(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _seed_complete_user(store)
    delegate = AsyncMock()
    send = AsyncMock()
    core = _core(store, delegate=delegate, send=send)
    install(core)

    message = SimpleNamespace(
        message_id="ordinary-writing-1",
        sender="49123",
        text="اكتبلي ايميل بالألماني لتأكيد موعد المدرسة.",
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    delegate.assert_awaited_once_with(message)
    send.assert_not_awaited()


def test_production_entrypoint_installs_grounding_before_closed_beta_wrapper() -> None:
    source = Path("webhook_security.py").read_text(encoding="utf-8")
    import_line = (
        "import writing_grounding_extensions as writing_grounding_layer  # noqa: E402"
    )
    install_line = "writing_grounding_layer.install(reminder_language_layer.core)"
    beta_line = "closed_beta_runtime_layer.install("

    assert import_line in source
    assert install_line in source
    assert source.index(install_line) < source.index(beta_line)
