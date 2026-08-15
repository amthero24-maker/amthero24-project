"""Tests for deterministic cancellation draft grounding and assistance."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import official_draft_runtime_extension as official_runtime
from cancellation_draft_grounding import (
    build_cancellation_missing_fields_help,
    build_cancellation_plain_explanation,
    ground_cancellation_draft,
)
from cancellation_grounding_extensions import install as install_cancellation_grounding
from conversation_intelligence import detect_language
from data_store import JsonDataStore
from official_draft_delivery import DRAFT_MARKER, END_MARKER, EXPLANATION_MARKER


_REQUEST = """اكتبلي رسالة إلغاء بالألماني ولا ترسلها. عندي اشتراك نادي رياضي:

Anbieter: MusterFit GmbH
Vertragsnummer: TEST-K-731
Vertragsbeginn: 01.03.2026

ما عندي معلومة مؤكدة عن مدة الإلغاء أو تاريخ نهاية العقد. بدي ألغي بأقرب موعد ممكن، واطلب منهم تأكيد خطي لتاريخ انتهاء العقد.
"""

_REQUEST_WITH_DEBIT = _REQUEST + "\nوكمان اطلب منهم يوقفوا الخصم المباشر من حسابي."

_UNGROUNDED_DRAFT = """MusterFit GmbH
[Straße und Hausnummer]
[Postleitzahl und Ort]

[Ihr Vor- und Nachname]
[Ihre Straße und Hausnummer]
[Ihre Postleitzahl und Ihr Ort]

[Datum]

Betreff: Kündigung des Vertrags Nr. TEST-K-731

Sehr geehrte Damen und Herren,

hiermit kündige ich meinen Mitgliedsvertrag mit der Vertragsnummer TEST-K-731 zum nächstmöglichen Zeitpunkt.

Der Vertrag begann am 01.03.2026.

Bitte bestätigen Sie mir sowohl das Vertragsende als auch das Datum, zu dem die Kündigung wirksam wird.

Bitte stellen Sie sicher, dass keine weiteren Beiträge von meinem Konto abgebucht werden.

Mit freundlichen Grüßen

[Ihr Vor- und Nachname]"""

_SAFE_CONFIRMATION = (
    "Bitte bestätigen Sie mir schriftlich den Eingang dieser Kündigung sowie das Datum, "
    "zu dem der Vertrag endet."
)

_MODEL_REPLY = f"""{DRAFT_MARKER}
{_UNGROUNDED_DRAFT}
{EXPLANATION_MARKER}
المسودة تطلب الإلغاء ولم يتم إرسالها.
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


def _seed_draft(store: JsonDataStore, draft: str) -> None:
    _seed(store)
    store.update_user("49123", {
        "session_last_reply": draft,
        "last_assistant_reply": draft,
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


def test_grounder_removes_unsupported_debit_and_normalizes_confirmation() -> None:
    result = ground_cancellation_draft(
        _REQUEST,
        _UNGROUNDED_DRAFT,
        conversation_language="ar",
    )

    assert result.applicable is True
    assert result.rejection_reason == ""
    assert result.changed is True
    assert "abgebucht" not in result.draft
    assert "von meinem Konto" not in result.draft
    assert result.draft.count(_SAFE_CONFIRMATION) == 1
    assert "sowohl das Vertragsende" not in result.draft
    assert "Datum, zu dem die Kündigung wirksam wird" not in result.draft
    assert "MusterFit GmbH" in result.draft
    assert "TEST-K-731" in result.draft
    assert "01.03.2026" in result.draft
    assert "zum nächstmöglichen Zeitpunkt" in result.draft


def test_user_supplied_direct_debit_instruction_is_preserved() -> None:
    result = ground_cancellation_draft(
        _REQUEST_WITH_DEBIT,
        _UNGROUNDED_DRAFT,
        conversation_language="ar",
    )

    assert result.rejection_reason == ""
    assert "keine weiteren Beiträge von meinem Konto abgebucht" in result.draft
    assert result.draft.count(_SAFE_CONFIRMATION) == 1


def test_new_numeric_end_date_fails_closed() -> None:
    invented = _UNGROUNDED_DRAFT.replace(
        "zum nächstmöglichen Zeitpunkt",
        "zum 31.12.2026",
    )
    result = ground_cancellation_draft(
        _REQUEST,
        invented,
        conversation_language="ar",
    )

    assert result.applicable is True
    assert result.rejection_reason == "unsupported-date-added"


@pytest.mark.anyio
async def test_runtime_delivers_only_grounded_draft_then_role_specific_companion(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _seed(store)
    store.claim_message("cancel-ground-1", "49123", _REQUEST)
    send = AsyncMock()
    core = _core(store, reply=_MODEL_REPLY, send=send)
    _install(core)

    message = SimpleNamespace(
        message_id="cancel-ground-1",
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
    assert "abgebucht" not in draft
    assert "von meinem Konto" not in draft
    assert draft.count(_SAFE_CONFIRMATION) == 1
    assert DRAFT_MARKER not in draft
    assert EXPLANATION_MARKER not in draft
    assert END_MARKER not in draft

    assert companion.startswith("هذه مسودة لإلغاء العقد مع MusterFit GmbH")
    assert "العنوان البريدي للجهة المستلمة (الشارع ورقم المنزل)" in companion
    assert "الرمز البريدي والمدينة للجهة المستلمة" in companion
    assert "عنوانك البريدي (الشارع ورقم المنزل)" in companion
    assert "الرمز البريدي والمدينة لعنوانك" in companion
    assert "تاريخ كتابة الرسالة" in companion
    assert "1️⃣ ترجمة كاملة للعربية للفهم فقط" in companion
    assert "4️⃣ خطوات الإرسال والمتابعة" in companion

    profile = store.get_user("49123")
    assert profile["session_last_reply"] == draft
    assert profile["last_assistant_reply"] == draft
    assert store.snapshot()["messages"]["cancel-ground-1"]["status"] == "sent"


@pytest.mark.anyio
async def test_option_two_is_deterministic_and_preserves_clean_draft_context(tmp_path) -> None:
    grounded = ground_cancellation_draft(
        _REQUEST,
        _UNGROUNDED_DRAFT,
        conversation_language="ar",
    ).draft
    store = JsonDataStore(tmp_path / "store.json")
    _seed_draft(store, grounded)
    store.claim_message("cancel-explain-1", "49123", "2")
    send = AsyncMock()
    seen_messages: list[object] = []
    core = _core(
        store,
        reply="MODEL SHOULD NOT RUN",
        send=send,
        seen_messages=seen_messages,
    )
    _install(core)

    message = SimpleNamespace(
        message_id="cancel-explain-1",
        sender="49123",
        text="2",
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    send.assert_awaited_once()
    explanation = send.await_args.args[1]
    assert explanation.startswith("شرح مبسّط للمحتوى:")
    assert "MusterFit GmbH" in explanation
    assert "TEST-K-731" in explanation
    assert "01.03.2026" in explanation
    assert "في أقرب موعد ممكن" in explanation
    assert "تاريخ كتابة الرسالة" in explanation
    assert "لا يعني تلقائيًا تاريخ اليوم" in explanation
    assert "[Datum]" not in explanation
    assert "[Postleitzahl und Ort]" not in explanation
    assert "سأرسل" not in explanation
    assert "سوف أرسل" not in explanation
    assert "abgebucht" not in explanation
    assert seen_messages == []

    profile = store.get_user("49123")
    assert profile["session_last_reply"] == grounded
    assert profile["last_assistant_reply"] == grounded
    assert profile["conversation_summary"] == "safe prior summary"
    assert store.snapshot()["messages"]["cancel-explain-1"]["status"] == "sent"


@pytest.mark.anyio
async def test_option_three_uses_distinct_recipient_and_sender_postal_roles(tmp_path) -> None:
    grounded = ground_cancellation_draft(
        _REQUEST,
        _UNGROUNDED_DRAFT,
        conversation_language="ar",
    ).draft
    store = JsonDataStore(tmp_path / "store.json")
    _seed_draft(store, grounded)
    store.claim_message("cancel-fields-1", "49123", "3")
    send = AsyncMock()
    seen_messages: list[object] = []
    core = _core(
        store,
        reply="MODEL SHOULD NOT RUN",
        send=send,
        seen_messages=seen_messages,
    )
    _install(core)

    message = SimpleNamespace(
        message_id="cancel-fields-1",
        sender="49123",
        text="3",
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    send.assert_awaited_once()
    fields = send.await_args.args[1]
    assert "الرمز البريدي والمدينة للجهة المستلمة" in fields
    assert "الرمز البريدي والمدينة لعنوانك" in fields
    assert "العنوان البريدي للجهة المستلمة (الشارع ورقم المنزل)" in fields
    assert "عنوانك البريدي (الشارع ورقم المنزل)" in fields
    assert "تاريخ كتابة الرسالة" in fields
    assert "[Postleitzahl und Ort]" not in fields
    assert "[Ihre Postleitzahl und Ihr Ort]" not in fields
    assert seen_messages == []


def test_plain_explanation_and_field_help_ignore_non_cancellation_drafts() -> None:
    generic = """Betreff: Rückfrage

Sehr geehrte Damen und Herren,

bitte senden Sie mir die Unterlagen.

Mit freundlichen Grüßen
[Ihr Name]"""
    assert build_cancellation_plain_explanation(
        generic,
        conversation_language="ar",
    ) is None
    assert build_cancellation_missing_fields_help(
        generic,
        conversation_language="ar",
    ) is None
