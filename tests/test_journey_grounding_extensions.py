"""Runtime evidence for grounded refund, appointment, and contract drafts."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import cancellation_grounding_extensions as cancellation_runtime
import journey_grounding_extensions as journey_runtime
import official_draft_runtime_extension as official_runtime
from conversation_intelligence import detect_language
from data_store import JsonDataStore
from official_draft_delivery import DRAFT_MARKER, END_MARKER, EXPLANATION_MARKER

_REFUND_REQUEST = """اكتبلي رسالة طلب استرداد بالألماني ولا ترسلها.

Anbieter: MusterShop GmbH
Betrag: 79,90 EUR
Kaufdatum: 02.08.2026
Bestellnummer: TEST-R-218
المنتج لم يصل."""

_REFUND_DRAFT = """MusterShop GmbH
[Adresse]

Betreff: Bitte um Prüfung einer Rückerstattung – Bestellung TEST-R-218

Sehr geehrte Damen und Herren,

am 02.08.2026 habe ich eine Bestellung über 79,90 EUR aufgegeben. Das Produkt ist nicht angekommen. Bitte prüfen Sie die Rückerstattung des gezahlten Betrags.

Mit freundlichen Grüßen
[Ihr Name]"""

_APPOINTMENT_REQUEST = """اكتبلي رسالة بالألماني لتغيير الموعد ولا ترسلها.

Organisator: Bürgeramt Köln
Referenz: TEST-T-441
Bisheriger Termin: 20.08.2026 10:30
Neuer Wunschtermin: 25.08.2026 14:00
Ort: Ottoplatz 1"""

_APPOINTMENT_DRAFT = """Bürgeramt Köln
Ottoplatz 1

Betreff: Bitte um Terminverschiebung – TEST-T-441

Sehr geehrte Damen und Herren,

ich bitte darum, meinen Termin am 20.08.2026 um 10:30 Uhr auf den 25.08.2026 um 14:00 Uhr zu verschieben. Ort: Ottoplatz 1. Bitte bestätigen Sie mir die Änderung schriftlich.

Mit freundlichen Grüßen
[Ihr Name]"""

_CONTRACT_REQUEST = """اكتبلي رسالة استفسار بالألماني عن العقد ولا ترسلها.

Vertragspartner: MusterNet GmbH
Vertragsnummer: TEST-V-882
Betrag: 39,90 EUR
Vertragsdatum: 01.06.2026
لا أعرف مدة التجديد وأريد توضيحًا كتابيًا."""

_CONTRACT_DRAFT = """MusterNet GmbH
[Adresse]

Betreff: Rückfrage zum Vertrag TEST-V-882

Sehr geehrte Damen und Herren,

zu meinem Vertrag vom 01.06.2026 mit einem monatlichen Betrag von 39,90 EUR bitte ich um schriftliche Erläuterung der Verlängerungsklausel. Bitte teilen Sie mir mit, welche Verlängerungsdauer in meinem Vertrag vereinbart ist.

Mit freundlichen Grüßen
[Ihr Name]"""

_GENERIC_REQUEST = "اكتبلي رسالة قصيرة بالألماني للمدرسة ولا ترسلها."
_GENERIC_DRAFT = """Betreff: Rückfrage

Sehr geehrte Damen und Herren,

bitte senden Sie mir die benötigten Unterlagen.

Mit freundlichen Grüßen
[Ihr Name]"""

_CASES = (
    (
        "refund",
        _REFUND_REQUEST,
        _REFUND_DRAFT,
        "هذه مسودة لطلب مراجعة استرداد أو تعويض",
        "لا تعني أن الاسترداد تمت الموافقة عليه",
    ),
    (
        "appointment",
        _APPOINTMENT_REQUEST,
        _APPOINTMENT_DRAFT,
        "هذه مسودة لطلب تغيير أو تأجيل الموعد",
        "لا تعني أن الحجز أو التغيير أو الإلغاء تم بالفعل",
    ),
    (
        "contract",
        _CONTRACT_REQUEST,
        _CONTRACT_DRAFT,
        "هذه مسودة لطلب توضيح أو تأكيد كتابي بشأن العقد",
        "لا تتضمن حكمًا قانونيًا نهائيًا",
    ),
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
        "session_topic": "synthetic",
        "current_topic": "synthetic",
        "conversation_summary": "safe prior summary",
    })


def _model_envelope(draft: str) -> str:
    return (
        f"{DRAFT_MARKER}\n{draft}\n"
        f"{EXPLANATION_MARKER}\nتم إرسال الرسالة وأصبحت النتيجة مؤكدة.\n"
        f"{END_MARKER}"
    )


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
            "session_topic": "raw-model-state",
            "current_topic": "raw-model-state",
            "conversation_summary": "raw model state that must not survive grounding",
        })
        store.update_message_status(message.message_id, "sent")

    core.process_incoming = process_incoming
    return core


def _install(core: SimpleNamespace) -> None:
    official_runtime.install(core)
    cancellation_runtime.install(core, official_runtime)
    journey_runtime.install(core, official_runtime)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("journey", "user_text", "draft", "summary_marker", "boundary_marker"),
    _CASES,
)
async def test_runtime_delivers_grounded_draft_and_deterministic_companion(
    tmp_path,
    journey: str,
    user_text: str,
    draft: str,
    summary_marker: str,
    boundary_marker: str,
) -> None:
    store = JsonDataStore(tmp_path / f"{journey}.json")
    _seed(store)
    message_id = f"journey-{journey}-1"
    store.claim_message(message_id, "49123", user_text)
    send = AsyncMock()
    core = _core(store, reply=_model_envelope(draft), send=send)
    _install(core)

    message = SimpleNamespace(
        message_id=message_id,
        sender="49123",
        text=user_text,
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    assert send.await_count == 2
    delivered_draft = send.await_args_list[0].args[1]
    companion = send.await_args_list[1].args[1]
    assert delivered_draft == draft
    assert DRAFT_MARKER not in delivered_draft
    assert EXPLANATION_MARKER not in delivered_draft
    assert END_MARKER not in delivered_draft
    assert summary_marker in companion
    assert boundary_marker in companion
    assert "تم إرسال الرسالة" not in companion
    assert "1️⃣ ترجمة كاملة للعربية للفهم فقط" in companion
    assert "2️⃣ شرح مبسّط للمحتوى" in companion
    assert "4️⃣ خطوات الإرسال والمتابعة" in companion

    profile = store.get_user("49123")
    assert profile["session_last_reply"] == draft
    assert profile["last_assistant_reply"] == draft
    assert "raw model state" not in str(profile.get("conversation_summary") or "")
    assert store.snapshot()["messages"][message_id]["status"] == "sent"


@pytest.mark.anyio
@pytest.mark.parametrize(("journey", "_request", "draft", "_summary", "boundary"), _CASES)
async def test_option_two_is_deterministic_and_does_not_call_model(
    tmp_path,
    journey: str,
    _request: str,
    draft: str,
    _summary: str,
    boundary: str,
) -> None:
    store = JsonDataStore(tmp_path / f"{journey}-explain.json")
    _seed_draft(store, draft)
    message_id = f"journey-{journey}-explain"
    store.claim_message(message_id, "49123", "2")
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
        message_id=message_id,
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
    assert boundary in explanation
    assert "[Adresse]" not in explanation
    assert "[Ihr Name]" not in explanation
    assert store.get_user("49123")["session_last_reply"] == draft
    assert store.snapshot()["messages"][message_id]["status"] == "sent"


@pytest.mark.anyio
async def test_unsupported_refund_amount_fails_closed_without_persisting_model_draft(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "refund-failure.json")
    _seed(store)
    store.update_user("49123", {
        "session_last_reply": "safe prior assistant reply",
        "last_assistant_reply": "safe prior assistant reply",
        "conversation_summary": "safe prior summary",
    })
    message_id = "journey-refund-failure"
    store.claim_message(message_id, "49123", _REFUND_REQUEST)
    unsafe = _REFUND_DRAFT.replace("79,90 EUR", "99,90 EUR")
    send = AsyncMock()
    core = _core(store, reply=_model_envelope(unsafe), send=send)
    _install(core)

    message = SimpleNamespace(
        message_id=message_id,
        sender="49123",
        text=_REFUND_REQUEST,
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    send.assert_awaited_once()
    failure = send.await_args.args[1]
    assert failure.startswith("لم أعرض المسودة")
    assert "لم أنفّذ أي إجراء خارجي" in failure
    assert "99,90 EUR" not in failure
    assert "MusterShop GmbH" not in failure

    profile = store.get_user("49123")
    assert profile["session_last_reply"] == "safe prior assistant reply"
    assert profile["last_assistant_reply"] == "safe prior assistant reply"
    assert profile["conversation_summary"] == "safe prior summary"
    assert "99,90 EUR" not in str(profile)
    assert store.snapshot()["messages"][message_id]["status"] == "sent"


@pytest.mark.anyio
async def test_generic_official_writing_remains_on_shared_copy_safe_path(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "generic.json")
    _seed(store)
    message_id = "journey-generic-1"
    store.claim_message(message_id, "49123", _GENERIC_REQUEST)
    send = AsyncMock()
    generic_reply = (
        f"{DRAFT_MARKER}\n{_GENERIC_DRAFT}\n"
        f"{EXPLANATION_MARKER}\nهذه مسودة عامة ولم تُرسل.\n{END_MARKER}"
    )
    core = _core(store, reply=generic_reply, send=send)
    _install(core)

    message = SimpleNamespace(
        message_id=message_id,
        sender="49123",
        text=_GENERIC_REQUEST,
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    assert send.await_count == 2
    assert send.await_args_list[0].args[1] == _GENERIC_DRAFT
    assert "هذه مسودة عامة ولم تُرسل" in send.await_args_list[1].args[1]


def test_production_wiring_orders_journey_grounding_before_narrow_writer() -> None:
    source = Path("webhook_security.py").read_text(encoding="utf-8")
    official_install = "official_draft_runtime_layer.install(reminder_language_layer.core)"
    cancellation_install = "cancellation_grounding_layer.install("
    journey_install = "journey_grounding_layer.install("
    writing_install = "writing_grounding_layer.install(reminder_language_layer.core)"
    beta_install = "closed_beta_runtime_layer.install("

    assert official_install in source
    assert cancellation_install in source
    assert journey_install in source
    assert writing_install in source
    assert beta_install in source
    assert source.index(official_install) < source.index(cancellation_install)
    assert source.index(cancellation_install) < source.index(journey_install)
    assert source.index(journey_install) < source.index(writing_install)
    assert source.index(writing_install) < source.index(beta_install)
