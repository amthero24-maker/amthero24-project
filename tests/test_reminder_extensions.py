"""Application-level reminder command tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

import reminder_extensions
from data_store import JsonDataStore


def _seed_user_and_mission(store: JsonDataStore) -> None:
    store.update_user("49123", {
        "memory_consent": "granted",
        "memory_consent_at": datetime.now(UTC).isoformat(),
        "memory_consent_version": "test-v1",
        "onboarding_stage": "complete",
        "intro_sent_at": datetime.now(UTC).isoformat(),
        "preferred_language": "ar",
        "first_name": "وسام",
        "current_topic": "residence",
    })
    memory = reminder_extensions.core.HeroMemory(store)
    memory.create_mission(
        "49123",
        title="موعد الإقامة",
        topic="residence",
        due_at="2099-08-10",
    )


@pytest.mark.anyio
async def test_reminder_command_creates_consent_backed_followup(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", "test-secret")
    store = JsonDataStore(tmp_path / "store.json")
    reminder_extensions.core.store = store
    reminder_extensions.core._hero_memory_store = reminder_extensions.core.HeroMemory(store)
    reminder_extensions._REMINDER_REPOSITORY = None
    _seed_user_and_mission(store)
    message = reminder_extensions.core.IncomingMessage(
        "reminder-create", "49123", "ذكرني قبلها بيوم", "text"
    )
    store.claim_message("reminder-create", "49123", message.text)

    with patch.object(reminder_extensions.core, "send_whatsapp_message", new=AsyncMock()) as send:
        await reminder_extensions.process_incoming(message)

    assert "رح ذكّرك" in send.await_args.args[1]
    reminders = reminder_extensions._repository().list("49123")
    assert len(reminders) == 1
    assert reminders[0]["title"] == "موعد الإقامة"
    assert reminders[0]["status"] == "pending"
    assert store.snapshot()["messages"]["reminder-create"]["status"] == "sent"


@pytest.mark.anyio
async def test_reminder_list_and_cancel_do_not_call_groq(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", "test-secret")
    store = JsonDataStore(tmp_path / "store.json")
    reminder_extensions.core.store = store
    reminder_extensions.core._hero_memory_store = reminder_extensions.core.HeroMemory(store)
    reminder_extensions._REMINDER_REPOSITORY = None
    _seed_user_and_mission(store)
    repository = reminder_extensions._repository()
    repository.create(
        "49123",
        title="موعد الإقامة",
        scheduled_at=datetime(2099, 8, 9, 7, tzinfo=UTC),
        language="ar",
    )

    with patch.object(reminder_extensions.core, "generate_reply", side_effect=AssertionError("Groq must not run")), patch.object(
        reminder_extensions.core, "send_whatsapp_message", new=AsyncMock()
    ) as send:
        list_message = reminder_extensions.core.IncomingMessage("rem-list", "49123", "شو تذكيراتي؟", "text")
        store.claim_message("rem-list", "49123", list_message.text)
        await reminder_extensions.process_incoming(list_message)
        assert "تذكيراتك" in send.await_args.args[1]

        cancel_message = reminder_extensions.core.IncomingMessage("rem-cancel", "49123", "ألغي التذكير", "text")
        store.claim_message("rem-cancel", "49123", cancel_message.text)
        await reminder_extensions.process_incoming(cancel_message)
        assert "ألغيت" in send.await_args.args[1]

    assert repository.list("49123") == []


def test_expired_delivery_lease_is_reclaimed_after_restart(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", "test-secret")
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    store = JsonDataStore(tmp_path / "store.json")
    repository = reminder_extensions.ResilientReminderRepository(store)
    reminder = repository.create(
        "49123",
        title="WKK",
        scheduled_at=now - timedelta(minutes=10),
        language="de",
    )

    def leave_stale_lease(data: dict) -> None:
        item = data["reminders"][reminder["reminder_id"]]
        item["status"] = "processing"
        item["lease_until"] = (now - timedelta(minutes=1)).isoformat()
        item["next_attempt_at"] = (now - timedelta(minutes=10)).isoformat()

    store._transaction(leave_stale_lease)
    claimed = repository.claim_due(now=now, limit=5)

    assert len(claimed) == 1
    assert claimed[0]["reminder_id"] == reminder["reminder_id"]
    assert claimed[0]["status"] == "processing"
    assert claimed[0]["last_error"] == "expired_delivery_lease"
