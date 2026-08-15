"""Runtime tests for grounded official-draft journey delivery."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import official_draft_runtime_extension as official_runtime
from cancellation_grounding_extensions import install as install_cancellation_grounding
from conversation_intelligence import detect_language
from data_store import JsonDataStore
from journey_grounding_extensions import install as install_journey_grounding
from official_draft_delivery import DRAFT_MARKER, END_MARKER, EXPLANATION_MARKER

_REFUND_DRAFT = """MusterShop GmbH
[Adresse]

Betreff: Bitte um Prüfung einer Rückerstattung – Bestellung TEST-R-218

Sehr geehrte Damen und Herren,

am 02.08.2026 habe ich eine Bestellung über 79,90 EUR aufgegeben. Das Produkt ist nicht angekommen. Bitte prüfen Sie die Rückerstattung des gezahlten Betrags.

Mit freundlichen Grüßen
[Ihr Name]"""

_REFUND_REQUEST = """اكتب رسالة طلب استرداد.
المزوّد: MusterShop GmbH
المبلغ: 79,90 EUR
تاريخ الشراء: 02.08.2026
رقم الطلب: TEST-R-218
المنتج لم يصل."""

_MODEL_REFUND_REPLY = (
    f"{DRAFT_MARKER}\n{_REFUND_DRAFT}\n"
    f"{EXPLANATION_MARKER}\nالمسودة تطلب استرداد المبلغ ولم تُرسل.\n{END_MARKER}"
)

def _seed(store: JsonDataStore) -> None:
    store.update_user("49123", {
        "memory_consent": "granted",
        "memory_consent_at": datetime.now(UTC).isoformat(),
        "memory_consent_version": "test-v1",
        "onboarding_stage": "complete",
        "preferred_language": "ar",
        "session_language": "ar",
    })


def _seed_draft(store: JsonDataStore, draft: str) -> None:
    _seed(store)
    store.update_user("49123", {
        "session_last_reply": draft,
        "last_assistant_reply": draft,
        "session_topic": "refund",
        "current_topic": "refund",
        "conversation_summary": "safe prior summary",
    })


def _core(
    store: JsonDataStore,
    *,
    reply: str,
    send: AsyncMock,
    seen_messages: list[object] | None = None,
) -> SimpleNamespace:
    core = SimpleNamespace(
        store=store,
        detect_language=detect_language,
        send_whatsapp_message=send,
        _session_expiry=lambda: (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
    )

    async def process_incoming(message) -> None:
        if seen_messages is not None:
            seen_messages.append(message)
        await core.send_whatsapp_message(message.sender, reply)
        store.update_user(message.sender, {
            "session_last_reply": reply,
            "last_assistant_reply": reply,
            "session_topic": "synthetic",
            "current_topic": "synthetic",
            "conversation_summary": "synthetic model state",
        })
        store.update_message_status(message.message_id, "sent")

    core.process_incoming = process_incoming
    return core


def _install(core: SimpleNamespace) -> None:
    official_runtime.install(core)
    install_cancellation_grounding(core, official_runtime)
    install_journey_grounding(core, official_runtime)


@pytest.mark.anyio
async def test_runtime_delivers_grounded_refund_draft_then_deterministic_companion(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _seed(store)
    request = _REFUND_REQUEST
    store.claim_message("refund-ground-1", "49123", request)
    send = AsyncMock()
    core = _core(store, reply=_MODEL_REFUND_REPLY, send=send)
    _install(core)

    message = SimpleNamespace(
        message_id="refund-ground-1",
        sender="49123",
        text=request,
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    assert send.await_count == 2
    draft = send.await_args_list[0].args[1]
    companion = send.await_args_list[1].args[1]
    assert draft == _REFUND_DRAFT
    assert DRAFT_MARKER not in draft
    assert EXPLANATION_MARKER not in draft
    assert END_MARKER not in draft
    assert companion.startswith("هذه مسودة لطلب مراجعة استرداد أو تعويض")
    assert "لم تُرسل" in companion
    assert "1️⃣ ترجمة كاملة للعربية للفهم فقط" in companion
    assert store.get_user("49123")["session_last_reply"] == _REFUND_DRAFT
    assert store.snapshot()["messages"]["refund-ground-1"]["status"] == "sent"


@pytest.mark.anyio
async def test_runtime_handles_unsafe_draft_without_generic_application_failure(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _seed(store)
    request = _REFUND_REQUEST
    store.claim_message("refund-reject-1", "49123", request)
    unsafe = _MODEL_REFUND_REPLY.replace("79,90 EUR", "99,90 EUR")
    send = AsyncMock()
    core = _core(store, reply=unsafe, send=send)
    _install(core)

    message = SimpleNamespace(
        message_id="refund-reject-1",
        sender="49123",
        text=request,
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    send.assert_awaited_once()
    handled = send.await_args.args[1]
    assert "لم أرسل المسودة" in handled
    assert "99,90" not in handled
    profile = store.get_user("49123")
    assert "session_last_reply" not in profile
    assert "last_assistant_reply" not in profile
    assert profile.get("conversation_summary") in {None, ""}
    assert store.snapshot()["messages"]["refund-reject-1"]["status"] == "sent"


@pytest.mark.anyio
async def test_option_two_is_deterministic_and_preserves_clean_journey_draft(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _seed_draft(store, _REFUND_DRAFT)
    store.claim_message("refund-explain-1", "49123", "2")
    send = AsyncMock()
    model_process = AsyncMock()
    core = SimpleNamespace(
        store=store,
        detect_language=detect_language,
        send_whatsapp_message=send,
        process_incoming=model_process,
        _session_expiry=lambda: (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
    )
    _install(core)

    message = SimpleNamespace(
        message_id="refund-explain-1",
        sender="49123",
        text="2",
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    model_process.assert_not_awaited()
    send.assert_awaited_once()
    explanation = send.await_args.args[1]
    assert explanation.startswith("شرح مبسّط للمحتوى:")
    assert "79,90 EUR" in explanation
    assert "02.08.2026" in explanation
    assert "TEST-R-218" in explanation
    assert "[Adresse]" not in explanation
    profile = store.get_user("49123")
    assert profile["session_last_reply"] == _REFUND_DRAFT
    assert profile["last_assistant_reply"] == _REFUND_DRAFT
    assert profile["conversation_summary"] == "safe prior summary"


def test_production_wiring_orders_all_draft_grounders_before_beta() -> None:
    source = Path("webhook_security.py").read_text(encoding="utf-8")
    official = "official_draft_runtime_layer.install(reminder_language_layer.core)"
    cancellation = "cancellation_grounding_layer.install("
    journey = "journey_grounding_layer.install("
    writing = "writing_grounding_layer.install(reminder_language_layer.core)"
    beta = "closed_beta_runtime_layer.install("

    for marker in (official, cancellation, journey, writing, beta):
        assert marker in source
    assert source.index(official) < source.index(cancellation)
    assert source.index(cancellation) < source.index(journey)
    assert source.index(journey) < source.index(writing)
    assert source.index(writing) < source.index(beta)


@pytest.mark.anyio
async def test_other_assistance_choices_remain_with_shared_runtime(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _seed_draft(store, _REFUND_DRAFT)
    store.claim_message("refund-translate-route-1", "49123", "1")
    send = AsyncMock()
    original_process = AsyncMock()
    core = SimpleNamespace(
        store=store,
        detect_language=detect_language,
        send_whatsapp_message=send,
        process_incoming=original_process,
        _session_expiry=lambda: (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
    )
    install_journey_grounding(core, official_runtime)

    message = SimpleNamespace(
        message_id="refund-translate-route-1",
        sender="49123",
        text="1",
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    original_process.assert_awaited_once_with(message)
    send.assert_not_awaited()


@pytest.mark.anyio
async def test_document_analysis_content_never_activates_journey_commands(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _seed(store)
    store.claim_message("internal-document-1", "49123", _REFUND_REQUEST)
    send = AsyncMock()
    original_process = AsyncMock()
    core = SimpleNamespace(
        store=store,
        detect_language=detect_language,
        send_whatsapp_message=send,
        process_incoming=original_process,
        _session_expiry=lambda: (datetime.now(UTC) + timedelta(hours=24)).isoformat(),
    )
    install_journey_grounding(core, official_runtime)

    message = SimpleNamespace(
        message_id="internal-document-1",
        sender="49123",
        text=_REFUND_REQUEST,
        message_type="text",
        internal_context="document_analysis",
    )
    await core.process_incoming(message)

    original_process.assert_awaited_once_with(message)
    send.assert_not_awaited()
