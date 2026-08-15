"""Runtime tests for product-wide copy-safe official draft delivery."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from conversation_intelligence import detect_language
from data_store import JsonDataStore
from draft_assistance import (
    ASSISTANCE_END_MARKER,
    ASSISTANCE_MARKER,
    DraftAssistanceFormatError,
)
from official_draft_delivery import (
    DRAFT_MARKER,
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

_CLEAN_DRAFT = """MusterFit GmbH
Kundenservice
[Adresse von MusterFit GmbH]

Betreff: Kündigung des Vertrags Nr. TEST-K-731

Sehr geehrte Damen und Herren,

hiermit kündige ich meinen Mitgliedsvertrag mit der Vertragsnummer TEST-K-731 zum nächstmöglichen Zeitpunkt.

Bitte bestätigen Sie mir schriftlich das Datum, zu dem das Vertragsverhältnis endet.

Mit freundlichen Grüßen
[Ihr Vor- und Nachname]
[Ihre Anschrift]
[Ihre Telefonnummer]
[Ihre E-Mail-Adresse]"""

_MODEL_REPLY = f"""{DRAFT_MARKER}
*Entwurf – Kündigung des Fitnessstudio-Vertrags*

{_CLEAN_DRAFT}
{EXPLANATION_MARKER}
المسودة تطلب إلغاء الاشتراك بأقرب موعد ممكن، ولم يتم إرسالها.
{END_MARKER}"""

_TRANSLATION_REPLY = f"""{ASSISTANCE_MARKER}
تطلب الرسالة إلغاء عقد العضوية ذي الرقم TEST-K-731 في أقرب موعد ممكن، كما تطلب تأكيد تاريخ انتهاء العقد خطيًا.
{ASSISTANCE_END_MARKER}"""


def _seed(store: JsonDataStore) -> None:
    store.update_user("49123", {
        "memory_consent": "granted",
        "memory_consent_at": datetime.now(UTC).isoformat(),
        "memory_consent_version": "test-v1",
        "onboarding_stage": "complete",
        "preferred_language": "ar",
        "session_language": "ar",
    })


def _seed_clean_draft(store: JsonDataStore) -> None:
    _seed(store)
    store.update_user("49123", {
        "session_last_reply": _CLEAN_DRAFT,
        "last_assistant_reply": _CLEAN_DRAFT,
        "session_topic": "cancellation",
        "current_topic": "cancellation",
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
            "conversation_summary": "synthetic assistance state",
        })
        store.update_message_status(message.message_id, "sent")

    core.process_incoming = process_incoming
    return core


@pytest.mark.anyio
async def test_runtime_delivers_draft_and_understanding_companion_as_two_messages(tmp_path) -> None:
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
    companion = send.await_args_list[1].args[1]
    assert draft.startswith("MusterFit GmbH")
    assert "Entwurf" not in draft
    assert "المسودة" not in draft
    assert DRAFT_MARKER not in draft
    assert EXPLANATION_MARKER not in draft
    assert END_MARKER not in draft
    assert companion.startswith("المسودة تطلب إلغاء الاشتراك")
    assert "الاسم الكامل" in companion
    assert "البريد الإلكتروني" in companion
    assert "1️⃣ ترجمة كاملة للعربية للفهم فقط" in companion
    assert "4️⃣ خطوات الإرسال والمتابعة" in companion

    profile = store.get_user("49123")
    assert profile["session_last_reply"] == draft
    assert profile["last_assistant_reply"] == draft
    assert is_official_draft_turn(
        "عدّل المسودة وخليها أقصر.",
        profile,
    ) is True
    assert store.snapshot()["messages"]["cancel-1"]["status"] == "sent"


@pytest.mark.anyio
async def test_secondary_companion_failure_does_not_fail_primary_draft(tmp_path) -> None:
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
    draft = send.await_args_list[0].args[1]
    assert "Sehr geehrte Damen und Herren" in draft
    assert store.snapshot()["messages"]["cancel-2"]["status"] == "sent"
    profile = store.get_user("49123")
    assert profile["session_last_reply"] == draft
    assert profile["last_assistant_reply"] == draft


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
    profile = store.get_user("49123")
    assert profile["session_last_reply"] == clarification
    assert is_official_draft_turn("عدّل المسودة.", profile) is False


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


@pytest.mark.anyio
async def test_translation_choice_is_isolated_and_preserves_clean_draft_context(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _seed_clean_draft(store)
    store.claim_message("translate-1", "49123", "1")
    send = AsyncMock()
    seen_messages: list[object] = []
    core = _core(
        store,
        reply=_TRANSLATION_REPLY,
        send=send,
        seen_messages=seen_messages,
    )
    install(core)

    message = SimpleNamespace(
        message_id="translate-1",
        sender="49123",
        text="1",
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    send.assert_awaited_once()
    translation = send.await_args.args[1]
    assert translation.startswith("ترجمة للفهم فقط")
    assert "لا ترسل هذه النسخة بدل المسودة الأصلية" in translation
    assert ASSISTANCE_MARKER not in translation
    assert ASSISTANCE_END_MARKER not in translation
    assert len(seen_messages) == 1
    assert getattr(seen_messages[0], "internal_context") == "official_draft_assistance"
    assert "TEST-K-731" not in getattr(seen_messages[0], "text")

    profile = store.get_user("49123")
    assert profile["session_last_reply"] == _CLEAN_DRAFT
    assert profile["last_assistant_reply"] == _CLEAN_DRAFT
    assert profile["session_topic"] == "cancellation"
    assert profile["current_topic"] == "cancellation"
    assert profile["conversation_summary"] == "safe prior summary"
    assert store.snapshot()["messages"]["translate-1"]["status"] == "sent"


@pytest.mark.anyio
async def test_missing_field_choice_is_deterministic_and_does_not_call_model(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _seed_clean_draft(store)
    store.claim_message("fields-1", "49123", "الخيار ٣")
    send = AsyncMock()
    model_process = AsyncMock()
    core = SimpleNamespace(
        store=store,
        detect_language=detect_language,
        send_whatsapp_message=send,
        process_incoming=model_process,
    )
    install(core)

    message = SimpleNamespace(
        message_id="fields-1",
        sender="49123",
        text="الخيار ٣",
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    model_process.assert_not_awaited()
    send.assert_awaited_once()
    help_text = send.await_args.args[1]
    assert "الحقول التي تحتاج مراجعة أو تعبئة" in help_text
    assert "الاسم الكامل" in help_text
    assert "البريد الإلكتروني" in help_text
    assert "عدّل المسودة بهذه البيانات:" in help_text
    profile = store.get_user("49123")
    assert profile["session_last_reply"] == _CLEAN_DRAFT
    assert profile["last_assistant_reply"] == _CLEAN_DRAFT
    assert store.snapshot()["messages"]["fields-1"]["status"] == "sent"


@pytest.mark.anyio
async def test_malformed_assistance_envelope_fails_closed_without_losing_draft(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _seed_clean_draft(store)
    store.claim_message("assist-bad-1", "49123", "اشرحلي")
    send = AsyncMock()
    core = _core(
        store,
        reply=f"{ASSISTANCE_MARKER}\nشرح بلا علامة نهاية",
        send=send,
    )
    install(core)

    message = SimpleNamespace(
        message_id="assist-bad-1",
        sender="49123",
        text="اشرحلي",
        message_type="text",
        internal_context="",
    )
    with pytest.raises(DraftAssistanceFormatError):
        await core.process_incoming(message)

    send.assert_not_awaited()
    profile = store.get_user("49123")
    assert profile["session_last_reply"] == _CLEAN_DRAFT
    assert profile["last_assistant_reply"] == _CLEAN_DRAFT


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
