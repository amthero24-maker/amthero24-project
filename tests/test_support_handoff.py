"""Human-support intent, encryption, persistence, privacy, and retention tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from data_store import JsonDataStore
from support_handoff import (
    SupportRepository,
    classify_category,
    classify_urgency,
    detect_support_intent,
)


def _repository(tmp_path, monkeypatch) -> SupportRepository:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("HUMAN_SUPPORT_ENABLED", "true")
    monkeypatch.setenv("SUPPORT_API_TOKEN", "support-secret")
    monkeypatch.setenv("SUPPORT_ENCRYPTION_KEY", "encryption-secret")
    return SupportRepository(JsonDataStore(tmp_path / "store.json"))


def test_multilingual_support_commands_and_classification() -> None:
    assert detect_support_intent("بدي احكي مع شخص") is not None
    assert detect_support_intent("I want human support").action == "request"
    assert detect_support_intent("حالة طلب الدعم").action == "status"
    assert detect_support_intent("الغي طلب الدعم").action == "cancel"
    assert classify_category("عندي مشكلة تقنية وما عم يشتغل") == "technical"
    assert classify_category("بدي حدا يشرحلي هالمستند") == "document"
    assert classify_urgency("المهلة بكرا") == "high"
    assert classify_urgency("وقت تفضوا") == "normal"


def test_ticket_stores_no_message_content_and_contact_is_encrypted(tmp_path, monkeypatch) -> None:
    repository = _repository(tmp_path, monkeypatch)
    phone = "491234567"
    private_text = "بدي احكي مع شخص عن مستند فيه رقم تأمين 123456"
    ticket = repository.create(
        phone,
        language="ar",
        category=classify_category(private_text),
        urgency=classify_urgency(private_text),
    )

    snapshot = repository.store.snapshot()
    serialized = str(snapshot["support_tickets"])
    assert ticket["status"] == "open"
    assert phone not in serialized
    assert private_text not in serialized
    assert "123456" not in serialized
    listed = repository.list_admin(status="open")
    assert listed[0]["contact"] == phone
    assert "contact_ciphertext" not in listed[0]


def test_duplicate_request_reuses_open_ticket_and_user_can_cancel(tmp_path, monkeypatch) -> None:
    repository = _repository(tmp_path, monkeypatch)
    first = repository.create("49123", language="de", category="general", urgency="normal")
    second = repository.create("49123", language="de", category="technical", urgency="high")

    assert first["ticket_id"] == second["ticket_id"]
    assert second["_operation"] == "existing"
    cancelled = repository.cancel_latest("49123")
    assert cancelled is not None and cancelled["status"] == "cancelled"
    assert repository.latest_for_user("49123")["status"] == "cancelled"


def test_admin_updates_status_and_aggregate_is_anonymous(tmp_path, monkeypatch) -> None:
    repository = _repository(tmp_path, monkeypatch)
    ticket = repository.create("49123", language="en", category="privacy", urgency="high")
    assigned = repository.update_admin_status(ticket["ticket_id"], "assigned")
    resolved = repository.update_admin_status(ticket["ticket_id"], "resolved")
    overview = repository.aggregate()

    assert assigned["status"] == "assigned"
    assert resolved["status"] == "resolved"
    assert overview["by_status"] == {"resolved": 1}
    assert "49123" not in str(overview)


def test_delete_and_retention_remove_linked_or_old_tickets(tmp_path, monkeypatch) -> None:
    repository = _repository(tmp_path, monkeypatch)
    ticket = repository.create("49123", language="ar", category="general", urgency="normal")
    repository.update_admin_status(ticket["ticket_id"], "resolved")

    def age(data: dict) -> None:
        data["support_tickets"][ticket["ticket_id"]]["updated_at"] = datetime(2026, 1, 1, tzinfo=UTC).isoformat()
    repository.store._transaction(age)
    assert repository.cleanup(now=datetime(2026, 7, 26, tzinfo=UTC), resolved_days=90) == 1

    repository.create("49124", language="de", category="general", urgency="normal")
    assert repository.delete_user("49124") is True
    assert repository.latest_for_user("49124") is None
