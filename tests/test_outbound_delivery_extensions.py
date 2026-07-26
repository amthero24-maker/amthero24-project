"""Composition tests for WhatsApp outbound acceptance and status webhooks."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("GROQ_API_KEY", "isolated-groq-key")
os.environ.setdefault("WHATSAPP_TOKEN", "isolated-whatsapp-token")
os.environ.setdefault("PHONE_NUMBER_ID", "isolated-phone-id")
os.environ.setdefault("VERIFY_TOKEN", "isolated-verify-token")
os.environ.setdefault("REMINDER_ENCRYPTION_KEY", "isolated-reminder-key-2026-safe")
os.environ.setdefault("SUPPORT_ENCRYPTION_KEY", "isolated-support-key-2026-safe")

import outbound_delivery_extensions as layer
from data_store import JsonDataStore
from outbound_delivery import DeliveryReceipt


def _reset_store(tmp_path, monkeypatch) -> JsonDataStore:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DURABLE_QUEUE_ENABLED", "false")
    layer.webhook_module.lifecycle.start_accepting()
    store = JsonDataStore(tmp_path / "delivery-extension.json")
    layer.core.store = store
    layer._DELIVERY_REPOSITORY = None
    return store


def _status_payload(message_id: str, status: str = "delivered") -> dict:
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "statuses": [{
                        "id": message_id,
                        "status": status,
                        "timestamp": "1786359600",
                        "recipient_id": "+491701234567",
                    }]
                }
            }]
        }]
    }
    if status == "failed":
        payload["entry"][0]["changes"][0]["value"]["statuses"][0]["errors"] = [{
            "code": 131047,
            "title": "private title",
            "message": "private failure text",
        }]
    return payload


@pytest.mark.anyio
async def test_successful_whatsapp_post_records_only_hashed_meta_id(tmp_path, monkeypatch) -> None:
    store = _reset_store(tmp_path, monkeypatch)
    raw_id = "wamid.response-private-id"
    raw_recipient = "+491701234567"

    async def fake_post(_payload):
        return {"contacts": [{"wa_id": raw_recipient}], "messages": [{"id": raw_id}]}

    monkeypatch.setattr(layer.whatsapp_module, "_post_message", fake_post)
    layer._install_send_tracking()
    response = await layer.whatsapp_module._post_message({"type": "text", "text": {"body": "private"}})

    assert response["messages"][0]["id"] == raw_id
    assert layer._repository(store).state(raw_id)["status"] == "accepted"
    encoded = json.dumps(store.snapshot(), ensure_ascii=False, sort_keys=True)
    assert raw_id not in encoded
    assert raw_recipient not in encoded
    assert "private" not in encoded


def test_status_only_webhook_updates_known_message_without_storing_recipient(tmp_path, monkeypatch) -> None:
    store = _reset_store(tmp_path, monkeypatch)
    message_id = "wamid.receipt-known"
    layer._repository(store).record_accepted(
        message_id,
        message_kind="template",
        now=datetime(2026, 8, 11, 8, 0, tzinfo=UTC),
    )

    response = TestClient(layer.app).post("/webhook", json=_status_payload(message_id))

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    assert layer._repository(store).state(message_id)["status"] == "delivered"
    encoded = json.dumps(store.snapshot(), ensure_ascii=False, sort_keys=True)
    assert message_id not in encoded
    assert "+491701234567" not in encoded


def test_unknown_receipt_is_acknowledged_without_creating_unbounded_record(tmp_path, monkeypatch) -> None:
    store = _reset_store(tmp_path, monkeypatch)

    response = TestClient(layer.app).post("/webhook", json=_status_payload("wamid.unknown-receipt", "read"))

    assert response.status_code == 200
    assert store.snapshot().get("outbound_delivery", {}) == {}


def test_failed_receipt_keeps_only_generic_code(tmp_path, monkeypatch) -> None:
    store = _reset_store(tmp_path, monkeypatch)
    message_id = "wamid.failed-receipt"
    layer._repository(store).record_accepted(message_id)

    response = TestClient(layer.app).post("/webhook", json=_status_payload(message_id, "failed"))

    assert response.status_code == 200
    state = layer._repository(store).state(message_id)
    assert state["status"] == "failed"
    assert state["failure_code"] == "131047"
    encoded = json.dumps(store.snapshot(), ensure_ascii=False, sort_keys=True)
    assert "private title" not in encoded
    assert "private failure text" not in encoded


def test_receipt_storage_outage_returns_retryable_status(tmp_path, monkeypatch) -> None:
    _reset_store(tmp_path, monkeypatch)
    message_id = "wamid.receipt-outage"
    layer._repository().record_accepted(message_id)

    def broken(_repository, _receipts):
        raise RuntimeError("synthetic database outage")

    monkeypatch.setattr(layer, "record_receipts", broken)
    response = TestClient(layer.app).post("/webhook", json=_status_payload(message_id))

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert response.headers["retry-after"] == "10"


def test_record_receipt_object_never_requires_recipient_identity(tmp_path, monkeypatch) -> None:
    store = _reset_store(tmp_path, monkeypatch)
    repository = layer._repository(store)
    message_id = "wamid.direct-receipt"
    repository.record_accepted(message_id)
    state = repository.record_receipt(DeliveryReceipt(
        message_id=message_id,
        status="read",
        occurred_at=datetime(2026, 8, 11, 9, 0, tzinfo=UTC),
    ))
    assert state == "read"
