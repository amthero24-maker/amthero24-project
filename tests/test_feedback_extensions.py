"""Application composition tests for anonymous feedback."""
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

import feedback_extensions
from data_store import JsonDataStore


def _seed_user(store: JsonDataStore) -> None:
    store.update_user("49123", {
        "memory_consent": "granted",
        "memory_consent_at": datetime.now(UTC).isoformat(),
        "memory_consent_version": "test-v1",
        "onboarding_stage": "complete",
        "preferred_language": "ar",
        "current_topic": "document",
    })


@pytest.mark.anyio
async def test_explicit_feedback_is_acknowledged_without_ai_call(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    feedback_extensions.core.store = store
    feedback_extensions._FEEDBACK_REPOSITORY = None
    _seed_user(store)
    message = feedback_extensions.core.IncomingMessage("feedback-1", "49123", "👍", "text")
    store.claim_message("feedback-1", "49123", "👍")

    with patch.object(feedback_extensions.core, "send_whatsapp_message", new=AsyncMock()) as send, patch.object(
        feedback_extensions, "_ORIGINAL_PROCESS_INCOMING", new=AsyncMock()
    ) as original:
        await feedback_extensions.process_incoming(message)

    original.assert_not_awaited()
    assert "مجهول" in send.await_args.args[1]
    assert store.snapshot()["messages"]["feedback-1"]["status"] == "sent"
    aggregate = feedback_extensions._repository(store).aggregate(days=30)
    assert aggregate["responses"] == 1
    assert aggregate["topics"] == {"document": 1}


@pytest.mark.anyio
async def test_normal_message_continues_to_existing_pipeline(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    feedback_extensions.core.store = JsonDataStore(tmp_path / "store.json")
    message = feedback_extensions.core.IncomingMessage("normal-1", "49123", "اشرحلي هالرسالة", "text")

    with patch.object(feedback_extensions, "_ORIGINAL_PROCESS_INCOMING", new=AsyncMock()) as original:
        await feedback_extensions.process_incoming(message)

    original.assert_awaited_once_with(message)
