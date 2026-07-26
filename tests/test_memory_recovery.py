"""Regression tests for smooth name and memory recovery."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

import app
from data_store import JsonDataStore
from onboarding import is_name_question


def _seed_memory_off(store: JsonDataStore, phone: str = "49123") -> None:
    store.update_user(phone, {
        "memory_consent": "declined",
        "memory_consent_at": datetime.now(UTC).isoformat(),
        "memory_consent_version": "test-v1",
        "onboarding_stage": "complete",
        "intro_sent_at": datetime.now(UTC).isoformat(),
        "session_language": "ar",
    })


def test_name_question_detection_is_localized() -> None:
    assert is_name_question("شو اسمي؟")
    assert is_name_question("Wie heiße ich?")
    assert is_name_question("What is my name?")
    assert not is_name_question("اسمي وسام")


@pytest.mark.anyio
async def test_name_question_restarts_consent_flow_for_memory_off_user(tmp_path) -> None:
    app.store = JsonDataStore(tmp_path / "store.json")
    _seed_memory_off(app.store)

    question = app.IncomingMessage("name-question", "49123", "شو اسمي؟", "text")
    app.store.claim_message(question.message_id, question.sender, question.text)
    with patch.object(app, "generate_reply", side_effect=AssertionError("Groq must not be called")), patch.object(app, "send_whatsapp_message", new=AsyncMock()) as send:
        await app.process_incoming(question)
    assert "شو بتحب ناديلك" in send.await_args.args[1]
    assert app.store.get_user("49123")["onboarding_stage"] == "awaiting_name"

    name = app.IncomingMessage("name-answer", "49123", "وسام", "text")
    app.store.claim_message(name.message_id, name.sender, name.text)
    with patch.object(app, "send_whatsapp_message", new=AsyncMock()) as send:
        await app.process_incoming(name)
    assert "أفعّل الذاكرة" in send.await_args.args[1]
    profile = app.store.get_user("49123")
    assert profile["pending_name"] == "وسام"
    assert "first_name" not in profile

    consent = app.IncomingMessage("name-consent", "49123", "نعم", "text")
    app.store.claim_message(consent.message_id, consent.sender, consent.text)
    with patch.object(app, "send_whatsapp_message", new=AsyncMock()):
        await app.process_incoming(consent)
    profile = app.store.get_user("49123")
    assert profile["memory_consent"] == "granted"
    assert profile["first_name"] == "وسام"


@pytest.mark.anyio
async def test_saved_name_question_is_deterministic_and_skips_groq(tmp_path) -> None:
    app.store = JsonDataStore(tmp_path / "store.json")
    app.store.update_user("49123", {
        "memory_consent": "granted",
        "memory_consent_at": datetime.now(UTC).isoformat(),
        "memory_consent_version": "test-v1",
        "onboarding_stage": "complete",
        "intro_sent_at": datetime.now(UTC).isoformat(),
        "preferred_language": "ar",
        "first_name": "وسام",
    })
    message = app.IncomingMessage("saved-name", "49123", "شو اسمي؟", "text")
    app.store.claim_message(message.message_id, message.sender, message.text)
    with patch.object(app, "generate_reply", side_effect=AssertionError("Groq must not be called")), patch.object(app, "send_whatsapp_message", new=AsyncMock()) as send:
        await app.process_incoming(message)
    assert send.await_args.args[1] == "اسمك وسام 🌿"


@pytest.mark.anyio
async def test_memory_summary_question_starts_name_step_when_memory_is_off(tmp_path) -> None:
    app.store = JsonDataStore(tmp_path / "store.json")
    _seed_memory_off(app.store)
    message = app.IncomingMessage("summary", "49123", "شو بتعرف عني؟", "text")
    app.store.claim_message(message.message_id, message.sender, message.text)
    with patch.object(app, "generate_reply", side_effect=AssertionError("Groq must not be called")), patch.object(app, "send_whatsapp_message", new=AsyncMock()) as send:
        await app.process_incoming(message)
    assert "شو بتحب ناديلك" in send.await_args.args[1]
    assert app.store.get_user("49123")["onboarding_stage"] == "awaiting_name"


@pytest.mark.anyio
async def test_explicit_name_reopens_consent_for_existing_memory_off_user(tmp_path) -> None:
    app.store = JsonDataStore(tmp_path / "store.json")
    _seed_memory_off(app.store)
    message = app.IncomingMessage("explicit-name", "49123", "اسمي وسام", "text")
    app.store.claim_message(message.message_id, message.sender, message.text)
    with patch.object(app, "send_whatsapp_message", new=AsyncMock()) as send:
        await app.process_incoming(message)
    assert "أفعّل الذاكرة" in send.await_args.args[1]
    profile = app.store.get_user("49123")
    assert profile["pending_name"] == "وسام"
    assert profile["onboarding_stage"] == "awaiting_consent"
