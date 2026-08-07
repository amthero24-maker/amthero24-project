"""Application and webhook tests."""
from __future__ import annotations

import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("VERIFY_TOKEN", "qa-token")

import app
from data_store import JsonDataStore


def payload(message: dict | None = None) -> dict:
    message = message or {
        "id": "wamid.1",
        "from": "49123",
        "type": "text",
        "text": {"body": "Hilfe"},
    }
    return {"entry": [{"changes": [{"value": {"messages": [message]}}]}]}


def seed_consented_user(store: JsonDataStore, phone: str, language: str = "de") -> None:
    store.update_user(phone, {
        "memory_consent": "granted",
        "memory_consent_at": datetime.now(UTC).isoformat(),
        "memory_consent_version": "test-v1",
        "onboarding_stage": "complete",
        "intro_sent_at": datetime.now(UTC).isoformat(),
        "preferred_language": language,
    })


def test_extract_text_button_interactive_and_ignore_statuses() -> None:
    assert app.extract_text_messages(payload()) == [("wamid.1", "49123", "Hilfe")]
    button = payload({"id": "2", "from": "49123", "type": "button", "button": {"text": "Ja"}})
    assert app.extract_text_messages(button) == [("2", "49123", "Ja")]
    interactive = payload({"id": "3", "from": "49123", "type": "interactive", "interactive": {"button_reply": {"id": "yes", "title": "Ja"}}})
    assert app.extract_text_messages(interactive) == [("3", "49123", "Ja")]
    assert app.extract_incoming_messages({"entry": [{"changes": [{"value": {"statuses": [{}]}}]}]}) == []


def test_extract_name_keeps_single_name_flow() -> None:
    assert app.extract_name("وسام") == "وسام"
    assert app.extract_name("مرحبا") == ""
    assert app.extract_name("تاني") == ""
    assert app.extract_name("عندي مشكلة") == ""
    assert app.extract_name("Mein Name ist Anna") == "Anna"


def test_verification_success_failure_and_missing_env() -> None:
    client = TestClient(app.app)
    with patch.dict(os.environ, {"VERIFY_TOKEN": "qa-token"}, clear=True):
        response = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "qa-token", "hub.challenge": "123"})
        assert response.status_code == 200
        assert response.text == "123"
        assert client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "123"}).status_code == 403
    with patch.dict(os.environ, {}, clear=True):
        assert client.get("/webhook").status_code == 503


def test_webhook_malformed_status_and_duplicate(tmp_path) -> None:
    client = TestClient(app.app)
    app.store = JsonDataStore(tmp_path / "store.json")
    assert client.post("/webhook", content=b"not-json", headers={"content-type": "application/json"}).status_code == 200
    assert client.post("/webhook", json={"entry": [{"changes": [{"value": {"statuses": [{}]}}]}]}).json() == {"status": "accepted"}
    with patch.object(app, "process_incoming", new=AsyncMock()):
        assert client.post("/webhook", json=payload()).status_code == 200
        assert client.post("/webhook", json=payload()).status_code == 200
    assert len(app.store.snapshot()["messages"]) == 1


@pytest.mark.anyio
async def test_new_user_gets_welcome_and_name_question_without_long_term_memory(tmp_path) -> None:
    app.store = JsonDataStore(tmp_path / "store.json")
    message = app.IncomingMessage("welcome", "49123", "مرحبا", "text")
    app.store.claim_message("welcome", "49123", message.text)
    with patch.object(app, "generate_reply", side_effect=AssertionError("Groq must not be called")), patch.object(app, "send_whatsapp_message", new=AsyncMock()) as send:
        await app.process_incoming(message)
    assert send.await_count == 1
    reply = send.await_args.args[1]
    assert "AmtHero24" in reply
    assert "شو بتحب ناديلك" in reply
    profile = app.store.get_user("49123")
    assert profile["onboarding_stage"] == "awaiting_name"
    assert "first_name" not in profile
    assert "preferred_language" not in profile
    assert profile["session_language"] == "ar"


@pytest.mark.anyio
async def test_name_is_pending_until_explicit_consent_then_saved(tmp_path) -> None:
    app.store = JsonDataStore(tmp_path / "store.json")
    app.store.update_user("49123", {
        "intro_sent_at": datetime.now(UTC).isoformat(),
        "onboarding_stage": "awaiting_name",
        "session_language": "ar",
    })
    name_message = app.IncomingMessage("name", "49123", "وسام", "text")
    app.store.claim_message("name", "49123", name_message.text)
    with patch.object(app, "send_whatsapp_message", new=AsyncMock()) as send:
        await app.process_incoming(name_message)
    assert "أفعّل الذاكرة" in send.await_args.args[1]
    profile = app.store.get_user("49123")
    assert profile["pending_name"] == "وسام"
    assert "first_name" not in profile
    assert profile["onboarding_stage"] == "awaiting_consent"

    yes_message = app.IncomingMessage("yes", "49123", "نعم", "text")
    app.store.claim_message("yes", "49123", yes_message.text)
    with patch.object(app, "send_whatsapp_message", new=AsyncMock()) as send:
        await app.process_incoming(yes_message)
    assert "فعّلت الذاكرة" in send.await_args.args[1]
    profile = app.store.get_user("49123")
    assert profile["memory_consent"] == "granted"
    assert profile["first_name"] == "وسام"
    assert profile["preferred_language"] == "ar"
    assert "pending_name" not in profile


@pytest.mark.anyio
async def test_declining_memory_removes_personal_memory_and_keeps_service_available(tmp_path) -> None:
    app.store = JsonDataStore(tmp_path / "store.json")
    app.store.update_user("49123", {
        "intro_sent_at": datetime.now(UTC).isoformat(),
        "onboarding_stage": "awaiting_consent",
        "pending_name": "وسام",
        "first_name": "legacy-name",
        "city": "Düsseldorf",
        "current_topic": "invoice",
        "session_language": "ar",
    })
    message = app.IncomingMessage("no", "49123", "لا", "text")
    app.store.claim_message("no", "49123", message.text)
    with patch.object(app, "send_whatsapp_message", new=AsyncMock()) as send:
        await app.process_incoming(message)
    assert "منكمل بدون ذاكرة شخصية" in send.await_args.args[1]
    profile = app.store.get_user("49123")
    assert profile["memory_consent"] == "declined"
    for key in ("first_name", "city", "current_topic", "pending_name"):
        assert key not in profile


@pytest.mark.anyio
async def test_process_incoming_success_and_groq_failure(tmp_path) -> None:
    app.store = JsonDataStore(tmp_path / "store.json")
    seed_consented_user(app.store, "49123", "de")
    message = app.IncomingMessage("one", "49123", "Hallo, ich brauche Hilfe", "text")
    app.store.claim_message("one", "49123", message.text)
    with patch.object(app, "generate_reply", return_value="Antwort"), patch.object(app, "send_whatsapp_message", new=AsyncMock()) as send:
        await app.process_incoming(message)
        send.assert_awaited_once_with("49123", "Antwort")
        assert app.store.snapshot()["messages"]["one"]["status"] == "sent"

    failed = app.IncomingMessage("two", "49123", "Ich brauche Hilfe", "text")
    app.store.claim_message("two", "49123", failed.text)
    with patch.object(app, "generate_reply", side_effect=RuntimeError("boom")), patch.object(app, "send_whatsapp_message", new=AsyncMock()) as send:
        await app.process_incoming(failed)
        assert send.await_count == 1
        assert app.store.snapshot()["messages"]["two"]["status"] == "failed"


@pytest.mark.anyio
async def test_product_language_question_is_authoritative_and_skips_groq(tmp_path) -> None:
    app.store = JsonDataStore(tmp_path / "store.json")
    seed_consented_user(app.store, "49123", "ar")
    message = app.IncomingMessage("languages", "49123", "شو اللغات يلي بتحكيها؟", "text")
    app.store.claim_message("languages", "49123", message.text)
    with patch.object(app, "generate_reply", side_effect=AssertionError("Groq must not be called")), patch.object(app, "send_whatsapp_message", new=AsyncMock()) as send:
        await app.process_incoming(message)
    reply = send.await_args.args[1]
    for language in ("العربية", "الألمانية", "الإنجليزية", "الأوكرانية", "اليونانية"):
        assert language in reply
    profile = app.store.get_user("49123")
    assert profile["preferred_language"] == "ar"
    assert profile["session_topic"] == "languages"
    assert profile.get("current_topic") != "languages"
    assert app.store.snapshot()["messages"]["languages"]["status"] == "sent"


@pytest.mark.anyio
async def test_media_download_failure_gets_safe_reply(tmp_path) -> None:
    app.store = JsonDataStore(tmp_path / "store.json")
    seed_consented_user(app.store, "49123", "de")
    message = app.IncomingMessage("image", "49123", "", "image", "media-1", "image/jpeg")
    app.store.claim_message("image", "49123", "", message_type="image", media_id="media-1")
    with patch.object(app, "get_media_url", new=AsyncMock(side_effect=RuntimeError("fail"))), patch.object(app, "send_whatsapp_message", new=AsyncMock()) as send:
        await app.process_incoming(message)
        assert send.await_count == 1
        assert app.store.snapshot()["messages"]["image"]["status"] == "failed"


def test_health_does_not_leak_secrets() -> None:
    response = TestClient(app.app).get("/health")
    body = response.text
    assert response.status_code == 200
    assert "WHATSAPP_TOKEN" not in body
    assert "GROQ_API_KEY" not in body
    assert "PHONE_NUMBER_ID" not in body


_FAST_PATH_CASES = (
    {
        "language": "ar",
        "identity": "من أنت؟",
        "chatgpt": "هل أنت ChatGPT أو تابع لـ OpenAI؟",
        "greeting": "مرحبا",
        "capabilities": "شو بتقدر تعمل؟",
        "identity_marker": "مساعد رقمي",
        "chatgpt_marker": "ولست ChatGPT",
        "greeting_marker": "AmtHero24",
        "capability_marker": "مستند",
    },
    {
        "language": "de",
        "identity": "Wer bist du?",
        "chatgpt": "Bist du ChatGPT oder OpenAI?",
        "greeting": "Hallo",
        "capabilities": "Was kannst du?",
        "identity_marker": "digitaler Assistent",
        "chatgpt_marker": "nicht ChatGPT",
        "greeting_marker": "AmtHero24",
        "capability_marker": "Dokument",
    },
    {
        "language": "en",
        "identity": "Who are you?",
        "chatgpt": "Are you ChatGPT or OpenAI?",
        "greeting": "Hello",
        "capabilities": "What can you do?",
        "identity_marker": "digital assistant",
        "chatgpt_marker": "not ChatGPT",
        "greeting_marker": "AmtHero24",
        "capability_marker": "document",
    },
    {
        "language": "uk",
        "identity": "Хто ти?",
        "chatgpt": "Ти ChatGPT або OpenAI?",
        "greeting": "Привіт",
        "capabilities": "Що ти можеш?",
        "identity_marker": "цифровий помічник",
        "chatgpt_marker": "не ChatGPT",
        "greeting_marker": "AmtHero24",
        "capability_marker": "документ",
    },
    {
        "language": "el",
        "identity": "Ποιος είσαι;",
        "chatgpt": "Είσαι ChatGPT ή OpenAI;",
        "greeting": "Γεια",
        "capabilities": "Τι μπορείς να κάνεις;",
        "identity_marker": "ψηφιακός βοηθός",
        "chatgpt_marker": "όχι το ChatGPT",
        "greeting_marker": "AmtHero24",
        "capability_marker": "εγγράφου",
    },
)


async def _assert_deterministic_fast_path(
    tmp_path,
    *,
    case: dict[str, str],
    field: str,
    expected_topic: str,
    marker_field: str,
) -> None:
    phone = "49123"
    app.store = JsonDataStore(tmp_path / f"{field}-{case['language']}.json")
    seed_consented_user(app.store, phone, case["language"])
    app.store.update_user(phone, {
        "current_topic": "housing",
        "session_topic": "housing",
        "last_message": "Mietvertrag prüfen",
        "last_assistant_reply": "Business reply",
    })
    message_id = f"{field}-{case['language']}"
    message = app.IncomingMessage(message_id, phone, case[field], "text")
    app.store.claim_message(message_id, phone, message.text)
    with patch.object(
        app, "generate_reply", side_effect=AssertionError("Groq must not be called")
    ), patch.object(app, "send_whatsapp_message", new=AsyncMock()) as send:
        await app.process_incoming(message)
    assert send.await_count == 1
    reply = send.await_args.args[1]
    assert case[marker_field].casefold() in reply.casefold()
    profile = app.store.get_user(phone)
    assert profile["current_topic"] == "housing"
    assert profile["session_topic"] == expected_topic
    assert profile["last_message"] == "Mietvertrag prüfen"
    assert profile["last_assistant_reply"] == "Business reply"
    assert profile["session_last_reply"] == reply
    assert app.store.snapshot()["messages"][message_id]["status"] == "sent"


@pytest.mark.anyio
@pytest.mark.parametrize("case", _FAST_PATH_CASES, ids=lambda item: item["language"])
async def test_identity_fast_path_is_localized_and_preserves_mission_topic(tmp_path, case) -> None:
    await _assert_deterministic_fast_path(
        tmp_path,
        case=case,
        field="identity",
        expected_topic="identity",
        marker_field="identity_marker",
    )


@pytest.mark.anyio
@pytest.mark.parametrize("case", _FAST_PATH_CASES, ids=lambda item: item["language"])
async def test_chatgpt_fast_path_is_localized_and_preserves_mission_topic(tmp_path, case) -> None:
    await _assert_deterministic_fast_path(
        tmp_path,
        case=case,
        field="chatgpt",
        expected_topic="identity",
        marker_field="chatgpt_marker",
    )


@pytest.mark.anyio
@pytest.mark.parametrize("case", _FAST_PATH_CASES, ids=lambda item: item["language"])
async def test_capability_fast_path_is_localized_and_preserves_mission_topic(tmp_path, case) -> None:
    await _assert_deterministic_fast_path(
        tmp_path,
        case=case,
        field="capabilities",
        expected_topic="capabilities",
        marker_field="capability_marker",
    )


@pytest.mark.anyio
@pytest.mark.parametrize("case", _FAST_PATH_CASES, ids=lambda item: item["language"])
async def test_greeting_fast_path_is_localized_and_preserves_mission_topic(tmp_path, case) -> None:
    await _assert_deterministic_fast_path(
        tmp_path,
        case=case,
        field="greeting",
        expected_topic="greeting",
        marker_field="greeting_marker",
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("command", "instruction"),
    (("اختصر", "more briefly"), ("اشرح أكثر", "Explain the previous answer")),
)
async def test_arabic_refinement_commands_use_session_reply_without_polluting_mission_context(
    tmp_path, command, instruction,
) -> None:
    phone = "49123"
    app.store = JsonDataStore(tmp_path / f"refine-{len(command)}.json")
    seed_consented_user(app.store, phone, "ar")
    app.store.update_user(phone, {
        "current_topic": "housing",
        "session_topic": "capabilities",
        "session_last_reply": "هذا هو الجواب السابق عن الخدمة.",
        "last_message": "Mietvertrag prüfen",
        "last_assistant_reply": "Business reply",
    })
    message_id = f"refine-{len(command)}"
    message = app.IncomingMessage(message_id, phone, command, "text")
    app.store.claim_message(message_id, phone, message.text)
    captured: dict[str, object] = {}

    def fake_generate_reply(**kwargs):
        captured.update(kwargs)
        return "صياغة محسّنة"

    with patch.object(app, "generate_reply", side_effect=fake_generate_reply), patch.object(
        app, "send_whatsapp_message", new=AsyncMock()
    ) as send:
        await app.process_incoming(message)

    assert instruction in str(captured["user_text"])
    assert "هذا هو الجواب السابق" in str(captured["user_text"])
    send.assert_awaited_once_with(phone, "صياغة محسّنة")
    profile = app.store.get_user(phone)
    assert profile["current_topic"] == "housing"
    assert profile["session_topic"] == "capabilities"
    assert profile["last_message"] == "Mietvertrag prüfen"
    assert profile["last_assistant_reply"] == "Business reply"
    assert profile["session_last_reply"] == "صياغة محسّنة"
