"""Application and webhook tests."""
from __future__ import annotations

import os
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
    assert client.post("/webhook", json=payload()).status_code == 200
    assert client.post("/webhook", json=payload()).status_code == 200
    assert len(app.store.snapshot()["messages"]) == 1


@pytest.mark.anyio
async def test_process_incoming_success_and_groq_failure(tmp_path) -> None:
    app.store = JsonDataStore(tmp_path / "store.json")
    message = app.IncomingMessage("one", "49123", "Hallo", "text")
    app.store.claim_message("one", "49123", "Hallo")
    with patch.object(app, "generate_reply", return_value="Antwort"), patch.object(app, "send_whatsapp_message", new=AsyncMock()) as send:
        await app.process_incoming(message)
        send.assert_awaited_once_with("49123", "Antwort")
        assert app.store.snapshot()["messages"]["one"]["status"] == "sent"

    failed = app.IncomingMessage("two", "49123", "Hallo", "text")
    app.store.claim_message("two", "49123", "Hallo")
    with patch.object(app, "generate_reply", side_effect=RuntimeError("boom")), patch.object(app, "send_whatsapp_message", new=AsyncMock()) as send:
        await app.process_incoming(failed)
        assert send.await_count == 1
        assert app.store.snapshot()["messages"]["two"]["status"] == "failed"


@pytest.mark.anyio
async def test_product_language_question_is_authoritative_and_skips_groq(tmp_path) -> None:
    app.store = JsonDataStore(tmp_path / "store.json")
    message = app.IncomingMessage("languages", "49123", "شو اللغات يلي بتحكيها؟", "text")
    app.store.claim_message("languages", "49123", message.text)
    with patch.object(app, "generate_reply", side_effect=AssertionError("Groq must not be called")), patch.object(app, "send_whatsapp_message", new=AsyncMock()) as send:
        await app.process_incoming(message)
    reply = send.await_args.args[1]
    for language in ("العربية", "الألمانية", "الإنجليزية", "الأوكرانية", "اليونانية"):
        assert language in reply
    profile = app.store.get_user("49123")
    assert profile["preferred_language"] == "ar"
    assert profile["current_topic"] == "languages"
    assert app.store.snapshot()["messages"]["languages"]["status"] == "sent"


@pytest.mark.anyio
async def test_media_download_failure_gets_safe_reply(tmp_path) -> None:
    app.store = JsonDataStore(tmp_path / "store.json")
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
