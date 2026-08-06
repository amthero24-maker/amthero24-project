"""Privacy deletion, export, and retention tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from data_store import JsonDataStore
from hero_memory import HeroMemory
from privacy_engine import cleanup_retention, delete_all_user_data, export_user_data
from reminder_engine import ReminderRepository


def _seed_everything(store: JsonDataStore, phone: str, monkeypatch) -> tuple[HeroMemory, ReminderRepository]:
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", "privacy-test-secret")
    store.update_user(phone, {
        "first_name": "وسام",
        "preferred_language": "ar",
        "memory_consent": "granted",
        "memory_consent_at": datetime.now(UTC).isoformat(),
        "memory_consent_version": "test-v1",
    })
    store.claim_message("m-1", phone, "رسالة خاصة")
    memory = HeroMemory(store)
    memory.record_consent(phone, "granted", "test-v1")
    mission = memory.create_mission(phone, title="WKK", topic="invoice")
    reminders = ReminderRepository(store)
    reminders.create(
        phone,
        title="WKK",
        mission_id=mission["mission_id"],
        scheduled_at=datetime(2099, 8, 10, 7, tzinfo=UTC),
        language="ar",
    )
    return memory, reminders


def test_complete_deletion_removes_all_user_linked_json_data(tmp_path, monkeypatch) -> None:
    phone = "491234567"
    store = JsonDataStore(tmp_path / "store.json")
    _seed_everything(store, phone, monkeypatch)

    assert delete_all_user_data(store, phone) is True
    snapshot = store.snapshot()

    assert snapshot["users"] == {}
    assert snapshot["messages"] == {}
    assert snapshot["cases"] == {}
    assert snapshot["reminders"] == {}
    assert snapshot["audit_log"] == []
    assert len(snapshot["privacy_events"]) == 1
    anonymous_event = snapshot["privacy_events"][0]
    assert anonymous_event["action"] == "user_data_deleted"
    serialized = str(anonymous_event)
    assert phone not in serialized
    assert "phone_hash" not in anonymous_event


def test_export_includes_safe_reminders_without_delivery_secrets(tmp_path, monkeypatch) -> None:
    phone = "491234567"
    store = JsonDataStore(tmp_path / "store.json")
    memory, reminders = _seed_everything(store, phone, monkeypatch)
    delivered_at = datetime(2026, 8, 6, 10, tzinfo=UTC)
    reminder = reminders.list(phone)[0]
    reminders.mark_sent(reminder["reminder_id"], now=delivered_at)
    assert reminders.acknowledge_recent(
        phone, now=delivered_at + timedelta(minutes=1),
    )[0] == "acknowledged"

    payload = export_user_data(store, phone, memory.export_user_data(phone), reminders)

    assert payload["profile"]["first_name"] == "وسام"
    assert payload["missions"][0]["title"] == "WKK"
    assert payload["reminders"][0]["title"] == "WKK"
    assert payload["reminders"][0]["status"] == "acknowledged"
    assert "acknowledged_at" in payload["reminders"][0]
    assert "acknowledged_sent_at" not in payload["reminders"][0]
    assert "recipient_ciphertext" not in payload["reminders"][0]
    assert "phone_hash" not in payload["reminders"][0]
    assert "last_error" not in payload["reminders"][0]


def test_retention_removes_old_closed_records_but_keeps_active_work(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", "privacy-test-secret")
    phone = "491234567"
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    store = JsonDataStore(tmp_path / "store.json")
    store.update_user(phone, {"memory_consent": "granted"})
    memory = HeroMemory(store)
    old_completed = memory.create_mission(phone, title="قديم", topic="invoice")
    memory.create_mission(phone, title="مفتوح", topic="residence")
    reminders = ReminderRepository(store)
    old_reminder = reminders.create(
        phone, title="قديم", scheduled_at=now - timedelta(days=200), language="ar"
    )
    active_reminder = reminders.create(
        phone, title="قادم", scheduled_at=now + timedelta(days=10), language="ar"
    )

    def age_records(data: dict) -> None:
        completed = data["cases"][old_completed["mission_id"]]
        completed["status"] = "completed"
        completed["completed_at"] = (now - timedelta(days=800)).isoformat()
        completed["updated_at"] = completed["completed_at"]
        sent = data["reminders"][old_reminder["reminder_id"]]
        sent["status"] = "acknowledged"
        sent["updated_at"] = (now - timedelta(days=100)).isoformat()
        data["audit_log"].append({
            "phone_hash": "old",
            "decision": "granted",
            "created_at": (now - timedelta(days=2000)).isoformat(),
        })

    store._transaction(age_records)
    result = cleanup_retention(store, now=now)
    snapshot = store.snapshot()

    assert result["missions"] == 1
    assert result["reminders"] == 1
    assert result["consent"] == 1
    assert old_completed["mission_id"] not in snapshot["cases"]
    assert active_reminder["reminder_id"] in snapshot["reminders"]
    assert any(record.get("title") == "مفتوح" for record in snapshot["cases"].values())
