"""Application-level mission flow tests."""
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

import app
from data_store import JsonDataStore


def seed_consented_user(store: JsonDataStore, phone: str = "49123") -> None:
    store.update_user(phone, {
        "memory_consent": "granted",
        "memory_consent_at": datetime.now(UTC).isoformat(),
        "memory_consent_version": "test-v1",
        "onboarding_stage": "complete",
        "intro_sent_at": datetime.now(UTC).isoformat(),
        "preferred_language": "ar",
        "current_topic": "invoice",
        "last_message": "فاتورة WKK",
    })


@pytest.mark.anyio
async def test_create_list_and_complete_mission_without_groq(tmp_path) -> None:
    app.store = JsonDataStore(tmp_path / "store.json")
    seed_consented_user(app.store)

    create = app.IncomingMessage("create", "49123", "تابعلي هالموضوع فاتورة WKK", "text")
    app.store.claim_message(create.message_id, create.sender, create.text)
    with patch.object(app, "generate_reply", side_effect=AssertionError("Groq must not be called")), patch.object(app, "send_whatsapp_message", new=AsyncMock()) as send:
        await app.process_incoming(create)
    assert "مهمة مفتوحة" in send.await_args.args[1]

    listing = app.IncomingMessage("list", "49123", "شو مهامي؟", "text")
    app.store.claim_message(listing.message_id, listing.sender, listing.text)
    with patch.object(app, "generate_reply", side_effect=AssertionError("Groq must not be called")), patch.object(app, "send_whatsapp_message", new=AsyncMock()) as send:
        await app.process_incoming(listing)
    assert "فاتورة WKK" in send.await_args.args[1]

    complete = app.IncomingMessage("complete", "49123", "خلصت المهمة", "text")
    app.store.claim_message(complete.message_id, complete.sender, complete.text)
    with patch.object(app, "generate_reply", side_effect=AssertionError("Groq must not be called")), patch.object(app, "send_whatsapp_message", new=AsyncMock()) as send:
        await app.process_incoming(complete)
    assert "مكتملة" in send.await_args.args[1]


@pytest.mark.anyio
async def test_mission_requires_memory_consent(tmp_path) -> None:
    app.store = JsonDataStore(tmp_path / "store.json")
    app.store.update_user("49123", {
        "memory_consent": "declined",
        "onboarding_stage": "complete",
        "intro_sent_at": datetime.now(UTC).isoformat(),
        "session_language": "ar",
    })
    message = app.IncomingMessage("mission", "49123", "تابعلي هالموضوع", "text")
    app.store.claim_message(message.message_id, message.sender, message.text)
    with patch.object(app, "send_whatsapp_message", new=AsyncMock()) as send:
        await app.process_incoming(message)
    assert "فعّل الذاكرة" in send.await_args.args[1]
    assert app.store.snapshot()["cases"] == {}
