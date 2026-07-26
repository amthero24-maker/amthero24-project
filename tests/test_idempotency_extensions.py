"""Production route tests for durable webhook claims and retry responses."""
from __future__ import annotations

import os

from fastapi.testclient import TestClient

os.environ.setdefault("GROQ_API_KEY", "isolated-groq-key")
os.environ.setdefault("WHATSAPP_TOKEN", "isolated-whatsapp-token")
os.environ.setdefault("PHONE_NUMBER_ID", "isolated-phone-id")
os.environ.setdefault("VERIFY_TOKEN", "isolated-verify-token")
os.environ.setdefault("REMINDER_ENCRYPTION_KEY", "isolated-reminder-key-2026-safe")
os.environ.setdefault("SUPPORT_ENCRYPTION_KEY", "isolated-support-key-2026-safe")

import idempotency_extensions as layer
from data_store import JsonDataStore


def _payload(message_id: str = "wamid.retry-safe") -> dict:
    return {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "id": message_id,
                        "from": "491701234567",
                        "type": "text",
                        "text": {"body": "Hallo"},
                    }]
                }
            }]
        }]
    }


def _reset_store(tmp_path, monkeypatch) -> JsonDataStore:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "webhook-idempotency.json")
    layer.core.store = store
    layer._MESSAGE_REPOSITORY = None
    return store


def test_duplicate_delivery_while_processing_runs_once(tmp_path, monkeypatch) -> None:
    store = _reset_store(tmp_path, monkeypatch)
    calls: list[str] = []

    async def process(message) -> None:
        calls.append(message.message_id)

    monkeypatch.setattr(layer.core, "process_incoming", process)
    client = TestClient(layer.app)

    assert client.post("/webhook", json=_payload()).status_code == 200
    assert client.post("/webhook", json=_payload()).status_code == 200

    assert calls == ["wamid.retry-safe"]
    assert store.snapshot()["messages"]["wamid.retry-safe"]["status"] == "processing"


def test_failed_background_processing_is_reclaimed_on_exact_retry(tmp_path, monkeypatch) -> None:
    store = _reset_store(tmp_path, monkeypatch)
    calls = 0

    async def process(message) -> None:
        nonlocal calls
        calls += 1
        store.update_message_status(message.message_id, "failed" if calls == 1 else "sent")

    monkeypatch.setattr(layer.core, "process_incoming", process)
    client = TestClient(layer.app)

    assert client.post("/webhook", json=_payload("wamid.failed-retry")).status_code == 200
    assert client.post("/webhook", json=_payload("wamid.failed-retry")).status_code == 200

    state = layer._repository(store).state("wamid.failed-retry")
    assert calls == 2
    assert state["attempt_count"] == 2
    assert state["status"] == "sent"


def test_unhandled_composition_exception_releases_claim_as_failed(tmp_path, monkeypatch) -> None:
    store = _reset_store(tmp_path, monkeypatch)

    async def broken(_message) -> None:
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(layer.core, "process_incoming", broken)
    response = TestClient(layer.app).post("/webhook", json=_payload("wamid.unhandled"))

    assert response.status_code == 200
    assert store.snapshot()["messages"]["wamid.unhandled"]["status"] == "failed"


def test_claim_storage_failure_returns_retryable_http_status(tmp_path, monkeypatch) -> None:
    _reset_store(tmp_path, monkeypatch)

    class BrokenRepository:
        def claim(self, *_args, **_kwargs):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(layer, "_repository", lambda store=None: BrokenRepository())
    response = TestClient(layer.app).post("/webhook", json=_payload("wamid.db-failure"))

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert response.headers["retry-after"] == "5"


def test_status_only_and_malformed_webhooks_remain_safely_acknowledged(tmp_path, monkeypatch) -> None:
    _reset_store(tmp_path, monkeypatch)
    client = TestClient(layer.app)

    status_payload = {"entry": [{"changes": [{"value": {"statuses": [{}]}}]}]}
    assert client.post("/webhook", json=status_payload).json() == {"status": "accepted"}
    assert client.post(
        "/webhook",
        content=b"not-json",
        headers={"content-type": "application/json"},
    ).json() == {"status": "accepted"}
