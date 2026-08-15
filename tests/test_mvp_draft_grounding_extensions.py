"""Runtime tests for grounded Refund, appointment, and contract drafts."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import official_draft_runtime_extension as official_runtime
from conversation_intelligence import detect_language
from data_store import JsonDataStore
from mvp_draft_grounding_extensions import install as install_mvp_grounding
from official_draft_delivery import DRAFT_MARKER, END_MARKER, EXPLANATION_MARKER

_REFUND_REQUEST = """اكتبلي طلب استرداد بالألماني ولا ترسله.
Anbieter: SyntheticShop GmbH
Bestellnummer: TEST-R-22
Betrag: 19,90 EUR
Datum: 01.08.2026
السبب: تم الخصم مرتين.
"""
_REFUND_DRAFT = """SyntheticShop GmbH

Betreff: Rückerstattung – Bestellnummer TEST-R-22

Sehr geehrte Damen und Herren,

zu meiner Bestellung TEST-R-22 wurde am 01.08.2026 ein Betrag von 19,90 EUR doppelt abgebucht. Ich bitte um Prüfung und Rückerstattung des doppelt berechneten Betrags.

Bitte bestätigen Sie den Eingang dieser Anfrage schriftlich.

Mit freundlichen Grüßen
[Ihr Name]"""

_APPOINTMENT_REQUEST = """اكتبلي رسالة لتأجيل الموعد بالألماني ولا ترسلها.
Organisator: Bürgeramt Aachen
Terminreferenz: TEST-T-9
Termin: 20.08.2026 um 10:00 Uhr
Ort: Musterstraße 10, Aachen
ما عندي موعد بديل؛ اطلب منهم اقتراح موعد جديد.
"""
_APPOINTMENT_DRAFT = """Bürgeramt Aachen
Musterstraße 10, Aachen

Betreff: Bitte um Verschiebung des Termins – TEST-T-9

Sehr geehrte Damen und Herren,

meinen Termin am 20.08.2026 um 10:00 Uhr kann ich leider nicht wahrnehmen. Bitte schlagen Sie mir einen neuen Termin vor.

Bitte bestätigen Sie mir die Änderung schriftlich.

Mit freundlichen Grüßen
[Ihr Name]"""

_CONTRACT_REQUEST = """اكتبلي رسالة استفسار بالألماني عن العقد ولا ترسلها.
Vertragspartner: MusterVertrag GmbH
Vertragsnummer: TEST-V-44
Betrag: 12,50 EUR
Datum: 01.09.2026
بدي توضيح مكتوب للبند المتعلق بالتجديد.
"""
_CONTRACT_DRAFT = """MusterVertrag GmbH

Betreff: Rückfrage zum Vertrag TEST-V-44

Sehr geehrte Damen und Herren,

bitte erläutern Sie mir schriftlich die Regelung zur Verlängerung meines Vertrags TEST-V-44 ab dem 01.09.2026 und wie sich der Betrag von 12,50 EUR zusammensetzt.

Mit freundlichen Grüßen
[Ihr Name]"""


def _envelope(draft: str, explanation: str = "UNTRUSTED MODEL EXPLANATION") -> str:
    return (
        f"{DRAFT_MARKER}\n{draft}\n"
        f"{EXPLANATION_MARKER}\n{explanation}\n{END_MARKER}"
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
        "session_topic": "writing",
        "current_topic": "writing",
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
    install_mvp_grounding(core, official_runtime)


@pytest.mark.anyio
async def test_safe_refund_draft_is_delivered_with_deterministic_companion(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _seed(store)
    store.claim_message("refund-ground-1", "49123", _REFUND_REQUEST)
    send = AsyncMock()
    core = _core(store, reply=_envelope(_REFUND_DRAFT), send=send)
    _install(core)

    message = SimpleNamespace(
        message_id="refund-ground-1",
        sender="49123",
        text=_REFUND_REQUEST,
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    assert send.await_count == 2
    draft = send.await_args_list[0].args[1]
    companion = send.await_args_list[1].args[1]
    assert draft == _REFUND_DRAFT
    assert "UNTRUSTED MODEL EXPLANATION" not in companion
    assert companion.startswith("هذه مسودة تطلب مراجعة استرداد بقيمة 19,90 EUR")
    assert "لم تُرسل ولا تضمن استرداد المال" in companion
    assert "1️⃣ ترجمة كاملة للعربية للفهم فقط" in companion
    assert "4️⃣ خطوات الإرسال والمتابعة" in companion
    assert DRAFT_MARKER not in draft
    assert EXPLANATION_MARKER not in draft
    assert END_MARKER not in draft

    profile = store.get_user("49123")
    assert profile["session_last_reply"] == _REFUND_DRAFT
    assert profile["last_assistant_reply"] == _REFUND_DRAFT
    assert store.snapshot()["messages"]["refund-ground-1"]["status"] == "sent"


@pytest.mark.anyio
async def test_invented_refund_amount_is_handled_without_failed_message_or_raw_memory(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _seed(store)
    store.claim_message("refund-reject-1", "49123", _REFUND_REQUEST)
    bad_draft = _REFUND_DRAFT.replace("19,90 EUR", "29,90 EUR")
    raw_reply = _envelope(bad_draft)
    send = AsyncMock()
    core = _core(store, reply=raw_reply, send=send)
    _install(core)

    message = SimpleNamespace(
        message_id="refund-reject-1",
        sender="49123",
        text=_REFUND_REQUEST,
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    send.assert_awaited_once()
    fallback = send.await_args.args[1]
    assert fallback.startswith("لم أرسل طلب الاسترداد")
    assert "29,90 EUR" not in fallback
    assert store.snapshot()["messages"]["refund-reject-1"]["status"] == "sent"
    profile = store.get_user("49123")
    assert profile["session_last_reply"] == fallback
    assert profile["last_assistant_reply"] == fallback
    assert "29,90 EUR" not in str(profile)


@pytest.mark.anyio
async def test_option_two_refund_explanation_is_deterministic_and_keeps_draft(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _seed_draft(store, _REFUND_DRAFT)
    store.claim_message("refund-explain-1", "49123", "2")
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
        message_id="refund-explain-1",
        sender="49123",
        text="2",
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    send.assert_awaited_once()
    explanation = send.await_args.args[1]
    assert explanation.startswith("شرح مبسّط للمحتوى:")
    assert "SyntheticShop GmbH" in explanation
    assert "19,90 EUR" in explanation
    assert "لا توجد ضمانة للاسترداد" in explanation
    assert seen_messages == []
    profile = store.get_user("49123")
    assert profile["session_last_reply"] == _REFUND_DRAFT
    assert profile["last_assistant_reply"] == _REFUND_DRAFT
    assert profile["conversation_summary"] == "safe prior summary"


@pytest.mark.anyio
async def test_safe_appointment_draft_and_option_two_preserve_confirmation_boundary(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _seed(store)
    store.claim_message("appointment-ground-1", "49123", _APPOINTMENT_REQUEST)
    send = AsyncMock()
    core = _core(store, reply=_envelope(_APPOINTMENT_DRAFT), send=send)
    _install(core)

    message = SimpleNamespace(
        message_id="appointment-ground-1",
        sender="49123",
        text=_APPOINTMENT_REQUEST,
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    assert send.await_count == 2
    assert send.await_args_list[0].args[1] == _APPOINTMENT_DRAFT
    companion = send.await_args_list[1].args[1]
    assert "Bürgeramt Aachen" in companion
    assert "20.08.2026 10:00" in companion
    assert "لا تعني أن الموعد حُجز أو تغيّر أو أُلغي بالفعل" in companion


@pytest.mark.anyio
async def test_contract_legal_conclusion_is_rejected_as_sent_safe_response(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _seed(store)
    store.claim_message("contract-reject-1", "49123", _CONTRACT_REQUEST)
    bad = _CONTRACT_DRAFT.replace(
        "bitte erläutern",
        "Die Klausel ist unwirksam. Bitte erläutern",
    )
    send = AsyncMock()
    core = _core(store, reply=_envelope(bad), send=send)
    _install(core)

    message = SimpleNamespace(
        message_id="contract-reject-1",
        sender="49123",
        text=_CONTRACT_REQUEST,
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    send.assert_awaited_once()
    assert send.await_args.args[1].startswith("لم أرسل رسالة العقد")
    assert store.snapshot()["messages"]["contract-reject-1"]["status"] == "sent"
    assert "unwirksam" not in str(store.get_user("49123"))


@pytest.mark.anyio
async def test_non_matching_general_draft_is_not_rewritten_by_grounding_layer(tmp_path) -> None:
    request = "اكتبلي رسالة شكر بالألماني ولا ترسلها."
    draft = """Betreff: Vielen Dank

Sehr geehrte Damen und Herren,

vielen Dank für Ihre Unterstützung.

Mit freundlichen Grüßen
[Ihr Name]"""
    raw = _envelope(draft, "MODEL EXPLANATION")
    store = JsonDataStore(tmp_path / "store.json")
    _seed(store)
    store.claim_message("generic-1", "49123", request)
    send = AsyncMock()
    core = _core(store, reply=raw, send=send)
    _install(core)

    message = SimpleNamespace(
        message_id="generic-1",
        sender="49123",
        text=request,
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    assert send.await_count == 2
    assert send.await_args_list[0].args[1] == draft
    assert "MODEL EXPLANATION" in send.await_args_list[1].args[1]


def test_production_wiring_places_journey_grounding_after_cancellation_before_narrow_writing() -> None:
    source = Path("webhook_security.py").read_text(encoding="utf-8")
    generic_install = "official_draft_runtime_layer.install(reminder_language_layer.core)"
    cancellation_install = "cancellation_grounding_layer.install("
    journey_install = "mvp_draft_grounding_layer.install("
    narrow_install = "writing_grounding_layer.install(reminder_language_layer.core)"
    beta_install = "closed_beta_runtime_layer.install("

    assert all(marker in source for marker in (
        generic_install,
        cancellation_install,
        journey_install,
        narrow_install,
        beta_install,
    ))
    assert source.index(generic_install) < source.index(cancellation_install)
    assert source.index(cancellation_install) < source.index(journey_install)
    assert source.index(journey_install) < source.index(narrow_install)
    assert source.index(narrow_install) < source.index(beta_install)
