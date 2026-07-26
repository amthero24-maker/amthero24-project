"""Unit tests for hashed WhatsApp outbound delivery tracking."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from admin_metrics import contains_personal_fields
from data_store import JsonDataStore
from outbound_delivery import (
    DeliveryReceipt,
    OutboundDeliveryRepository,
    extract_delivery_receipts,
    extract_response_message_ids,
)
from outbound_delivery_policy import augment_launch_report, outbound_delivery_check


def _repository(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("OUTBOUND_DELIVERY_RETENTION_DAYS", "30")
    store = JsonDataStore(tmp_path / "outbound-delivery.json")
    return store, OutboundDeliveryRepository(store)


def test_receipt_extraction_keeps_only_supported_operational_fields() -> None:
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "statuses": [
                        {
                            "id": "wamid.delivery-1",
                            "status": "failed",
                            "timestamp": "1786273200",
                            "recipient_id": "+491701234567",
                            "errors": [{
                                "code": 131047,
                                "title": "private title",
                                "message": "private error message",
                                "error_data": {"details": "private details"},
                            }],
                        },
                        {"id": "wamid.delivery-2", "status": "unsupported"},
                    ]
                }
            }]
        }]
    }

    receipts = extract_delivery_receipts(payload)

    assert receipts == [DeliveryReceipt(
        message_id="wamid.delivery-1",
        status="failed",
        occurred_at=datetime.fromtimestamp(1786273200, tz=UTC),
        failure_code="131047",
    )]
    encoded = json.dumps(receipts[0].__dict__, default=str)
    assert "+491701234567" not in encoded
    assert "private title" not in encoded
    assert "private error message" not in encoded
    assert "private details" not in encoded


def test_response_message_ids_are_bounded_to_unique_meta_ids() -> None:
    response = {
        "contacts": [{"wa_id": "491701234567"}],
        "messages": [
            {"id": "wamid.one"},
            {"id": "wamid.one"},
            {"id": "wamid.two"},
            {"missing": True},
        ],
    }
    assert extract_response_message_ids(response) == ["wamid.one", "wamid.two"]


def test_json_repository_hashes_ids_and_never_stores_recipient_or_content(tmp_path, monkeypatch) -> None:
    store, repository = _repository(tmp_path, monkeypatch)
    message_id = "wamid.private-outbound-identifier"
    now = datetime(2026, 8, 10, 9, 0, tzinfo=UTC)

    assert repository.record_accepted(message_id, message_kind="text", now=now) is True
    assert repository.record_accepted(message_id, message_kind="text", now=now) is False

    snapshot = store.snapshot()
    key = hashlib.sha256(message_id.encode()).hexdigest()
    assert list(snapshot["outbound_delivery"]) == [key]
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    assert message_id not in encoded
    assert "phone" not in encoded
    assert "recipient" not in encoded
    assert "message text" not in encoded
    assert repository.state(message_id)["status"] == "accepted"


def test_receipts_are_idempotent_and_success_can_recover_an_earlier_failure(tmp_path, monkeypatch) -> None:
    _, repository = _repository(tmp_path, monkeypatch)
    message_id = "wamid.lifecycle"
    start = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
    repository.record_accepted(message_id, message_kind="template", now=start)

    assert repository.record_receipt(DeliveryReceipt(message_id, "sent", start + timedelta(seconds=10)), now=start) == "sent"
    assert repository.record_receipt(DeliveryReceipt(message_id, "sent", start + timedelta(seconds=20)), now=start) == "sent"
    first_sent = repository.state(message_id)["sent_at"]
    assert first_sent == (start + timedelta(seconds=10)).isoformat()

    assert repository.record_receipt(
        DeliveryReceipt(message_id, "failed", start + timedelta(seconds=30), "131047"),
        now=start,
    ) == "failed"
    assert repository.record_receipt(
        DeliveryReceipt(message_id, "sent", start + timedelta(seconds=15)),
        now=start,
    ) == "failed"
    assert repository.record_receipt(
        DeliveryReceipt(message_id, "delivered", start + timedelta(seconds=40)),
        now=start,
    ) == "delivered"
    assert repository.record_receipt(
        DeliveryReceipt(message_id, "failed", start + timedelta(seconds=50), "131000"),
        now=start,
    ) == "delivered"
    assert repository.record_receipt(
        DeliveryReceipt(message_id, "read", start + timedelta(seconds=60)),
        now=start,
    ) == "read"

    state = repository.state(message_id)
    assert state["status"] == "read"
    assert state["failure_code"] == "131000"
    assert state["read_at"] == (start + timedelta(seconds=60)).isoformat()
    assert repository.record_receipt(
        DeliveryReceipt("wamid.unknown", "delivered", start),
        now=start,
    ) == "unknown"


def test_json_aggregate_and_cleanup_are_bounded_and_person_free(tmp_path, monkeypatch) -> None:
    store, repository = _repository(tmp_path, monkeypatch)
    current = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    states = {
        "accepted": "wamid.aggregate-accepted",
        "sent": "wamid.aggregate-sent",
        "delivered": "wamid.aggregate-delivered",
        "read": "wamid.aggregate-read",
        "failed": "wamid.aggregate-failed",
    }
    for offset, (status, message_id) in enumerate(states.items(), start=1):
        repository.record_accepted(message_id, now=current - timedelta(minutes=20 + offset))
        if status != "accepted":
            repository.record_receipt(
                DeliveryReceipt(message_id, status, current - timedelta(minutes=offset), "131047" if status == "failed" else ""),
                now=current,
            )

    overview = repository.aggregate(now=current)

    assert overview == {
        "tracked_24h": 5,
        "by_status": {"accepted": 1, "sent": 1, "delivered": 1, "read": 1, "failed": 1},
        "terminal_24h": 3,
        "delivery_success_pct": 66.7,
        "pending_over_15m": 2,
        "oldest_pending_age_seconds": 1320,
    }
    assert contains_personal_fields({"outbound_delivery": overview}) is False
    encoded = json.dumps(overview, ensure_ascii=False)
    for message_id in states.values():
        assert message_id not in encoded

    snapshot = store.snapshot()
    for record in snapshot["outbound_delivery"].values():
        record["expires_at"] = (current - timedelta(seconds=1)).isoformat()
    store._write_atomic(snapshot)
    assert repository.cleanup(now=current) == 5
    assert store.snapshot()["outbound_delivery"] == {}


def test_delivery_launch_policy_and_augmentation_are_idempotent() -> None:
    healthy = {
        "tracked_24h": 20,
        "by_status": {"accepted": 0, "sent": 0, "delivered": 10, "read": 10, "failed": 0},
        "terminal_24h": 20,
        "delivery_success_pct": 100.0,
        "pending_over_15m": 0,
        "oldest_pending_age_seconds": 0,
    }
    blocked = dict(healthy)
    blocked["by_status"] = {"accepted": 0, "sent": 0, "delivered": 2, "read": 0, "failed": 10}
    blocked["terminal_24h"] = 12
    blocked["delivery_success_pct"] = 16.7

    assert outbound_delivery_check({"outbound_delivery": healthy})["status"] == "ready"
    assert outbound_delivery_check({"outbound_delivery": blocked})["status"] == "blocked"
    assert outbound_delivery_check({
        "outbound_delivery": {**healthy, "pending_over_15m": 1, "oldest_pending_age_seconds": 901}
    })["status"] == "warning"

    base = {
        "status": "ready",
        "checks": [{"code": "postgresql", "status": "ready", "detail": "ok"}],
        "summary": {"ready": 1, "warning": 0, "blocked": 0},
        "next_actions": [],
    }
    once = augment_launch_report(base, {"outbound_delivery": blocked})
    twice = augment_launch_report(once, {"outbound_delivery": blocked})
    assert once == twice
    assert once["status"] == "blocked"
    assert [item["code"] for item in once["checks"]].count("outbound_delivery") == 1
