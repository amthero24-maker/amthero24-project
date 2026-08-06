"""Reminder engine unit and delivery tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from data_store import JsonDataStore
from reminder_engine import (
    ReminderRepository,
    ReminderServiceError,
    deliver_due_reminders,
    detect_reminder_intent,
    resolve_reminder_schedule,
    service_window_open,
)


def test_detects_arabic_relative_and_absolute_reminders() -> None:
    now = datetime(2026, 7, 26, 8, tzinfo=UTC)
    relative = detect_reminder_intent("ذكرني بعد 3 أيام", now=now)
    assert relative is not None
    assert relative.action == "create"
    assert relative.scheduled_at is not None

    absolute = detect_reminder_intent("ذكرني يوم 10.08.2026", now=now)
    assert absolute is not None
    assert absolute.scheduled_at is not None
    assert absolute.scheduled_at.strftime("%Y-%m-%d") == "2026-08-10"


def test_resolves_default_day_before_mission_deadline() -> None:
    now = datetime(2026, 7, 26, 8, tzinfo=UTC)
    intent = detect_reminder_intent("ذكرني قبل الموعد بيوم", now=now)
    assert intent is not None
    scheduled = resolve_reminder_schedule(intent, {"due_at": "2026-08-10"}, now=now)
    assert scheduled is not None
    assert scheduled.astimezone().date().isoformat() in {"2026-08-09", "2026-08-08"}


def test_json_repository_encrypts_recipient_and_deduplicates(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", "test-secret")
    store = JsonDataStore(tmp_path / "store.json")
    repository = ReminderRepository(store)
    scheduled = datetime(2026, 8, 10, 7, tzinfo=UTC)

    first = repository.create("491234567", title="WKK", scheduled_at=scheduled, language="ar")
    second = repository.create("491234567", title="WKK", scheduled_at=scheduled, language="ar")

    assert first["reminder_id"] == second["reminder_id"]
    raw = (tmp_path / "store.json").read_text(encoding="utf-8")
    assert "491234567" not in raw
    assert len(repository.list("491234567")) == 1
    assert repository.cancel("491234567") == 1
    assert repository.list("491234567") == []


def test_reschedule_requires_selection_when_multiple_reminders_exist(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", "reminder-key-2026-unique-7fA9xQ2mLp8V")
    store = JsonDataStore(tmp_path / "store.json")
    repository = ReminderRepository(store)
    first_at = datetime(2026, 8, 10, 7, tzinfo=UTC)
    second_at = datetime(2026, 8, 11, 7, tzinfo=UTC)
    repository.create("491234567", title="First", scheduled_at=first_at, language="en")
    repository.create("491234567", title="Second", scheduled_at=second_at, language="en")

    status, _ = repository.reschedule(
        "491234567", scheduled_at=datetime(2026, 8, 12, 7, tzinfo=UTC)
    )
    assert status == "ambiguous"
    assert [item["scheduled_at"] for item in repository.list("491234567")] == [
        first_at.isoformat(), second_at.isoformat(),
    ]

    conflict_at = datetime(2026, 8, 12, 7, tzinfo=UTC)
    repository.create("491234567", title="Second", scheduled_at=conflict_at, language="en")
    status, _ = repository.reschedule(
        "491234567", scheduled_at=conflict_at, position=2,
    )
    assert status == "conflict"

    status, updated = repository.reschedule(
        "491234567",
        scheduled_at=datetime(2026, 8, 13, 7, tzinfo=UTC),
        position=2,
    )
    assert status == "updated"
    assert updated["title"] == "Second"
    assert updated["scheduled_at"] == datetime(2026, 8, 13, 7, tzinfo=UTC).isoformat()
    assert updated["status"] == "pending"
    assert updated["attempt_count"] == 0


def test_cancel_selected_is_atomic_and_uses_displayed_order(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", "reminder-key-2026-unique-7fA9xQ2mLp8V")
    repository = ReminderRepository(JsonDataStore(tmp_path / "store.json"))
    phone = "491234567"
    start = datetime(2026, 8, 10, 7, tzinfo=UTC)
    for index, title in enumerate(("First", "Second", "Third")):
        repository.create(
            phone,
            title=title,
            scheduled_at=start + timedelta(days=index),
            language="en",
        )

    assert repository.cancel_selected(phone, (1, 4)) == 0
    assert [item["title"] for item in repository.list(phone)] == ["First", "Second", "Third"]

    assert repository.cancel_selected(phone, (2, 1, 2)) == 2
    assert [item["title"] for item in repository.list(phone)] == ["Third"]


def test_recurring_reminder_advances_in_local_time_and_finishes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", "reminder-key-2026-unique-7fA9xQ2mLp8V")
    repository = ReminderRepository(JsonDataStore(tmp_path / "store.json"))
    first = datetime(2026, 10, 24, 6, tzinfo=UTC)  # 08:00 Europe/Berlin before DST ends
    reminder = repository.create(
        "491234567", title="Medicine", scheduled_at=first, language="en",
        recurrence_days=1, recurrence_count=2,
    )

    repository.mark_sent(reminder["reminder_id"], now=first)
    active = repository.list("491234567")
    assert len(active) == 1
    assert active[0]["scheduled_at"] == datetime(2026, 10, 25, 7, tzinfo=UTC).isoformat()
    assert active[0]["recurrence_remaining"] == 1
    assert active[0]["status"] == "pending"

    repository.mark_sent(reminder["reminder_id"], now=datetime(2026, 10, 25, 7, tzinfo=UTC))
    assert repository.list("491234567") == []
    assert repository.list("491234567", active_only=False)[0]["status"] == "sent"


def test_recurrence_requires_bounded_valid_count(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", "reminder-key-2026-unique-7fA9xQ2mLp8V")
    repository = ReminderRepository(JsonDataStore(tmp_path / "store.json"))
    with pytest.raises(ReminderServiceError, match="invalid_recurrence"):
        repository.create(
            "491234567", title="Unsafe", scheduled_at=datetime.now(UTC) + timedelta(hours=1),
            language="en", recurrence_days=1, recurrence_count=500,
        )


def test_service_window_uses_last_inbound_activity() -> None:
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    assert service_window_open({"last_seen": (now - timedelta(hours=2)).isoformat()}, now=now)
    assert not service_window_open({"last_seen": (now - timedelta(hours=25)).isoformat()}, now=now)


@pytest.mark.anyio
async def test_due_delivery_uses_freeform_inside_service_window(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", "test-secret")
    phone = "491234567"
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    store = JsonDataStore(tmp_path / "store.json")
    store.update_user(phone, {
        "memory_consent": "granted",
        "preferred_language": "ar",
        "last_seen": (now - timedelta(hours=1)).isoformat(),
    })
    repository = ReminderRepository(store)
    repository.create(phone, title="موعد الإقامة", scheduled_at=now - timedelta(minutes=1), language="ar")
    send_text = AsyncMock()
    send_template = AsyncMock()

    result = await deliver_due_reminders(
        repository, store, send_text=send_text, send_template=send_template, now=now
    )

    assert result == {"claimed": 1, "sent": 1, "blocked": 0, "failed": 0}
    send_text.assert_awaited_once()
    send_template.assert_not_awaited()
    assert repository.list(phone, active_only=False)[0]["status"] == "sent"


@pytest.mark.anyio
async def test_due_delivery_requires_template_outside_service_window(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", "test-secret")
    phone = "491234567"
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    store = JsonDataStore(tmp_path / "store.json")
    store.update_user(phone, {
        "memory_consent": "granted",
        "preferred_language": "de",
        "last_seen": (now - timedelta(hours=30)).isoformat(),
    })
    repository = ReminderRepository(store)
    repository.create(phone, title="WKK", scheduled_at=now - timedelta(minutes=1), language="de")

    blocked = await deliver_due_reminders(
        repository,
        store,
        send_text=AsyncMock(),
        send_template=AsyncMock(),
        now=now,
        template_name="",
    )
    assert blocked["blocked"] == 1
    assert repository.list(phone, active_only=False)[0]["status"] == "blocked_template"
