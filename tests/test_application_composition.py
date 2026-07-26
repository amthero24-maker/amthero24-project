"""Production composition tests for relationship preferences."""
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

import application
from data_store import JsonDataStore


@pytest.mark.anyio
async def test_explicit_style_preference_is_saved_with_consent(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    application.core.store = JsonDataStore(tmp_path / "store.json")
    application.core._hero_memory_store = application.core.HeroMemory(application.core.store)
    application.core.store.update_user("49123", {
        "memory_consent": "granted",
        "memory_consent_at": datetime.now(UTC).isoformat(),
        "memory_consent_version": "test-v1",
        "onboarding_stage": "complete",
        "intro_sent_at": datetime.now(UTC).isoformat(),
        "preferred_language": "ar",
    })
    message = application.core.IncomingMessage("style-1", "49123", "من هلق جاوبني باختصار", "text")
    application.core.store.claim_message(message.message_id, message.sender, message.text)

    with patch.object(application.core, "generate_reply", side_effect=AssertionError("Groq must not be called")), patch.object(
        application.core, "send_whatsapp_message", new=AsyncMock()
    ) as send:
        await application.process_incoming(message)

    profile = application.core.store.get_user("49123")
    assert profile["communication_style"] == "detail=concise"
    assert "رح أتذكّر" in send.await_args.args[1]
    assert application.core.store.snapshot()["messages"]["style-1"]["status"] == "sent"


@pytest.mark.anyio
async def test_session_preference_without_consent_is_not_persisted(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    application.core.store = JsonDataStore(tmp_path / "store.json")
    application.core._hero_memory_store = application.core.HeroMemory(application.core.store)
    application.core.store.update_user("49123", {
        "memory_consent": "declined",
        "onboarding_stage": "complete",
        "intro_sent_at": datetime.now(UTC).isoformat(),
        "session_language": "ar",
    })
    message = application.core.IncomingMessage("style-2", "49123", "جاوبني باختصار", "text")
    application.core.store.claim_message(message.message_id, message.sender, message.text)

    with patch.object(application.core, "send_whatsapp_message", new=AsyncMock()) as send:
        await application.process_incoming(message)

    profile = application.core.store.get_user("49123")
    assert "communication_style" not in profile
    assert "بهالمحادثة" in send.await_args.args[1]
