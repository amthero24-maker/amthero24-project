"""Runtime tests for product-wide copy-safe official draft delivery."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from conversation_intelligence import detect_language
from data_store import JsonDataStore
from official_draft_delivery import (
    DRAFT_MARKER,
    DRAFT_OUTPUT_KIND,
    END_MARKER,
    EXPLANATION_MARKER,
    CopySafeDraftFormatError,
    is_official_draft_turn,
)
from official_draft_runtime_extension import install


_REQUEST = """اكتبلي رسالة إلغاء بالألماني ولا ترسلها. عندي اشتراك نادي رياضي:

Anbieter: MusterFit GmbH
Vertragsnummer: TEST-K-731
Vertragsbeginn: 01.03.2026

ما عندي معلومة مؤكدة عن مدة الإلغاء أو تاريخ نهاية العقد. بدي ألغي بأقرب موعد ممكن، واطلب منهم تأكيد خطي لتاريخ انتهاء العقد.
"""

_MODEL_REPLY = f"""{DRAFT_MARKER}
*Entwurf – Kündigung des Fitnessstudio-Vertrags*

MusterFit GmbH
Kundenservice
[Adresse von MusterFit GmbH]

Betreff: Kündigung des Vertrags Nr. TEST-K-731

Sehr geehrte Damen und Herren,

hiermit kündige ich meinen Mitgliedsvertrag mit der Vertragsnummer TEST-K-731 zum nächstmöglichen Zeitpunkt.

Bitte bestätigen Sie mir schriftlich das Datum, zu dem das Vertragsverhältnis endet.

Mit freundlichen Grüßen
[Ihr Vor- und Nachname]
{EXPLANATION_MARKER}
المسودة منفصلة ولم يتم إرسالها. راجع بياناتك ثم أرسلها بنفسك إذا كانت مناسبة.
{END_MARKER}"""


def _seed(store: JsonDataStore) -> None:
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
    reply: str,
    send: AsyncMock,
) -> SimpleNamespace:
    core = SimpleNamespace(
        store=store,
        detect_language=detect_language,
        send_whatsapp_message=send,
    )

    async def process_incoming(message) -> None:
        profile = store.get_user(message.sender)
        assert is_official_draft_turn("Restate the previous answer.", profile) is True
        await core.send_whatsapp_message(message.sender, reply)
        store.update_user(message.sender, {
            "session_last_reply": reply,
            "last_assistant_reply": reply,
        })
        store.update_message_status(message.message_id, "sent")

    core.process_incoming = process_incoming
    return core


@pytest.mark.anyio
async def test_runtime_delivers_draft_and_explanation_as_two_messages(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _seed(store)
    store.claim_message("cancel-1", "49123", _REQUEST)
    send = AsyncMock()
    core = _core(store, reply=_MODEL_REPLY, send=send)
    install(core)

    message = SimpleNamespace(
        message_id="cancel-1",
        sender="49123",
        text=_REQUEST,
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    assert send.await_count == 2
    draft = send.await_args_list[0].args[1]
    explanation = send.await_args_list[1].args[1]
    assert draft.startswith("MusterFit GmbH")
    assert "Entwurf" not in draft
    assert "المسودة" not in draft
    assert DRAFT_MARKER not in draft
    assert EXPLANATION_MARKER not in draft
    assert END_MARKER not in draft
    assert explanation.startswith("المسودة منفصلة")

    profile = store.get_user("49123")
    assert profile["session_output_kind"] == DRAFT_OUTPUT_KIND
    assert profile["session_last_reply"] == draft
    assert profile["last_assistant_reply"] == draft
    assert store.snapshot()["messages"]["cancel-1"]["status"] == "sent"


@pytest.mark.anyio
async def test_secondary_explanation_failure_does_not_fail_primary_draft(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _seed(store)
    store.claim_message("cancel-2", "49123", _REQUEST)
    send = AsyncMock(side_effect=[None, RuntimeError("secondary unavailable")])
    core = _core(store, reply=_MODEL_REPLY, send=send)
    install(core)

    message = SimpleNamespace(
        message_id="cancel-2",
        sender="49123",
        text=_REQUEST,
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    assert send.await_count == 2
    assert "Sehr geehrte Damen und Herren" in send.await_args_list[0].args[1]
    assert store.snapshot()["messages"]["cancel-2"]["status"] == "sent"
    assert store.get_user("49123")["session_output_kind"] == DRAFT_OUTPUT_KIND


@pytest.mark.anyio
async def test_focused_clarification_remains_one_ordinary_message(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _seed(store)
    store.claim_message("clarify-1", "49123", _REQUEST)
    send = AsyncMock()
    clarification = "Wie lautet der genaue Name des Vertragspartners?"
    core = _core(store, reply=clarification, send=send)
    install(core)

    message = SimpleNamespace(
        message_id="clarify-1",
        sender="49123",
        text=_REQUEST,
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    send.assert_awaited_once_with("49123", clarification)
    assert store.get_user("49123")["session_output_kind"] == "ordinary"


@pytest.mark.anyio
async def test_malformed_draft_envelope_is_not_delivered_mixed(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _seed(store)
    store.claim_message("bad-1", "49123", _REQUEST)
    send = AsyncMock()
    core = _core(
        store,
        reply=f"{DRAFT_MARKER}\nUnvollständige Ausgabe ohne weitere Marker",
        send=send,
    )
    install(core)

    message = SimpleNamespace(
        message_id="bad-1",
        sender="49123",
        text=_REQUEST,
        message_type="text",
        internal_context="",
    )
    with pytest.raises(CopySafeDraftFormatError):
        await core.process_incoming(message)
    send.assert_not_awaited()


def test_production_wiring_keeps_narrow_grounding_outside_generic_runtime() -> None:
    source = Path("webhook_security.py").read_text(encoding="utf-8")
    generic_install = "official_draft_runtime_layer.install(reminder_language_layer.core)"
    narrow_install = "writing_grounding_layer.install(reminder_language_layer.core)"
    beta_install = "closed_beta_runtime_layer.install("

    assert generic_install in source
    assert narrow_install in source
    assert beta_install in source
    assert source.index(generic_install) < source.index(narrow_install)
    assert source.index(narrow_install) < source.index(beta_install)
