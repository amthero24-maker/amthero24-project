"""Regression tests for persisted reminder clarification state."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import reminder_pending_storage  # noqa: F401
from data_store import JsonDataStore


def test_pending_reminder_fields_survive_store_allowlist(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    scheduled = datetime.now(UTC) + timedelta(minutes=1)

    store.update_user("49123", {
        "pending_reminder_title": "اكل",
        "pending_reminder_at": scheduled.isoformat(),
        "pending_reminder_recurrence_days": "1",
        "pending_reminder_recurrence_count": "7",
        "pending_reminder_weekdays_only": "1",
        "pending_reminder_weekdays": "0,3",
    })

    profile = store.get_user("49123")
    assert profile["pending_reminder_title"] == "اكل"
    assert profile["pending_reminder_at"] == scheduled.isoformat()
    assert profile["pending_reminder_recurrence_days"] == "1"
    assert profile["pending_reminder_recurrence_count"] == "7"
    assert profile["pending_reminder_weekdays_only"] == "1"
    assert profile["pending_reminder_weekdays"] == "0,3"


def test_pending_reminder_fields_are_session_scoped(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    now = datetime.now(UTC)
    store.update_user("49123", {
        "pending_reminder_title": "اكل",
        "pending_reminder_recurrence_days": "7",
        "pending_reminder_recurrence_count": "4",
        "pending_reminder_weekdays_only": "0",
        "pending_reminder_weekdays": "0,3",
        "session_expires_at": (now - timedelta(seconds=1)).isoformat(),
    })

    store.cleanup_expired(now=now)

    assert "pending_reminder_title" not in store.get_user("49123")
    assert "pending_reminder_recurrence_days" not in store.get_user("49123")
    assert "pending_reminder_recurrence_count" not in store.get_user("49123")
    assert "pending_reminder_weekdays_only" not in store.get_user("49123")
    assert "pending_reminder_weekdays" not in store.get_user("49123")
