"""Application-level privacy control tests."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

import privacy_extensions
from data_store import JsonDataStore
from reminder_engine import ReminderRepository


def _seed(store: JsonDataStore, monkeypatch) -> None:
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", "privacy-test-secret")
    store.update_user("49123", {
        "memory_consent": "granted",
        "memory_consent_at": datetime.now(UTC).isoformat(),
        "memory_consent_version": "test-v1",
        "onboarding_stage": "complete",
        "preferred_language": "ar",
        "first_name": "وسام",
    })
    memory = privacy_extensions.core.HeroMemory(store)
    memory.record_consent("49123", "granted", "test-v1")
    memory.create_mission("49123", title="WKK", topic="invoice")
    ReminderRepository(store).create(
        "49123",
        title="WKK",
        scheduled_at=datetime(2099, 8, 10, 7, tzinfo=UTC),
        language="ar",
    )


@pytest.mark.anyio
async def test_whatsapp_delete_command_removes_profile_missions_and_reminders(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    privacy_extensions.core.store = store
    privacy_extensions.core._hero_memory_store = privacy_extensions.core.HeroMemory(store)
    privacy_extensions.composed._REMINDER_REPOSITORY = None
    _seed(store, monkeypatch)
    message = privacy_extensions.core.IncomingMessage("delete-1", "49123", "امسح بياناتي", "text")
    store.claim_message("delete-1", "49123", message.text)

    with patch.object(privacy_extensions.core, "send_whatsapp_message", new=AsyncMock()) as send:
        await privacy_extensions.core.process_incoming(message)

    assert "تم حذف بياناتك الشخصية" in send.await_args.args[1]
    snapshot = store.snapshot()
    assert snapshot["users"] == {}
    assert snapshot["messages"] == {}
    assert snapshot["cases"] == {}
    assert snapshot["reminders"] == {}


@pytest.mark.anyio
async def test_whatsapp_export_mentions_active_reminder_without_ciphertext(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    privacy_extensions.core.store = store
    privacy_extensions.core._hero_memory_store = privacy_extensions.core.HeroMemory(store)
    privacy_extensions.composed._REMINDER_REPOSITORY = None
    _seed(store, monkeypatch)
    message = privacy_extensions.core.IncomingMessage("export-1", "49123", "نزّل بياناتي", "text")
    store.claim_message("export-1", "49123", message.text)

    with patch.object(privacy_extensions.core, "send_whatsapp_message", new=AsyncMock()) as send:
        await privacy_extensions.core.process_incoming(message)

    reply = send.await_args.args[1]
    assert "تذكيراتك" in reply
    assert "WKK" in reply
    assert "recipient_ciphertext" not in reply
