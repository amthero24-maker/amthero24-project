"""Privacy-safe aggregate admin metric tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from admin_metrics import build_overview, contains_personal_fields
from data_store import JsonDataStore
from document_action_repository import PendingDocumentRepository
from hero_memory import HeroMemory
from reminder_engine import ReminderRepository


def test_overview_aggregates_product_health_without_personal_data(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", "admin-test-secret")
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    phone = "491234567"
    store = JsonDataStore(tmp_path / "store.json")
    store.update_user(phone, {
        "first_name": "وسام",
        "city": "Düsseldorf",
        "preferred_language": "ar",
        "memory_consent": "granted",
        "last_seen": (now - timedelta(hours=2)).isoformat(),
        "last_message": "هذه رسالة شخصية جدًا",
    })
    store.claim_message("msg-1", phone, "نص شخصي", message_type="text")
    store.update_message_status("msg-1", "failed")
    memory = HeroMemory(store)
    memory.create_mission(phone, title="WKK", topic="invoice")
    reminder = ReminderRepository(store).create(
        phone,
        title="WKK",
        scheduled_at=now + timedelta(days=2),
        language="ar",
    )

    def add_sensitive_failure_text(data: dict) -> None:
        data["reminders"][reminder["reminder_id"]]["last_error"] = f"delivery failed for +{phone}"

    store._transaction(add_sensitive_failure_text)
    PendingDocumentRepository(store).put(phone, {"title": "Jobcenter", "topic": "jobcenter"}, now=now)

    overview = build_overview(store, now=now, version="2.3.0", model="test-model")
    serialized = str(overview)

    assert overview["users"]["total"] == 1
    assert overview["users"]["active_24h"] == 1
    assert overview["users"]["languages"] == {"ar": 1}
    assert overview["missions"]["by_status"] == {"open": 1}
    assert overview["reminders"]["by_status"] == {"pending": 1}
    assert overview["reminders"]["due_unsent"] == 0
    assert overview["reminders"]["unsent_recipients"] == 1
    assert overview["reminders"]["latest"] == {
        "status": "pending",
        "scheduled_at": (now + timedelta(days=2)).isoformat(),
        "attempt_count": 0,
        "last_error_code": "redacted",
        "next_attempt_at": (now + timedelta(days=2)).isoformat(),
        "lease_until": None,
        "sent_at": None,
    }
    assert overview["messages_24h"]["failed"] == 1
    assert overview["messages_24h"]["by_type"] == {"text": 1}
    assert overview["document_actions"]["pending"] == 1
    assert contains_personal_fields(overview) is False
    for forbidden in (phone, "وسام", "Düsseldorf", "رسالة شخصية", "نص شخصي"):
        assert forbidden not in serialized


def test_personal_field_guard_distinguishes_aggregate_text_category_from_content() -> None:
    assert contains_personal_fields({"messages_24h": {"by_type": {"text": 3}}}) is False
    assert contains_personal_fields({"messages": [{"text": "private content"}]}) is True
    assert contains_personal_fields({"by_type": {"text": {"sender": "private"}}}) is True
    assert contains_personal_fields({"by_type": {"text": "private content"}}) is True
