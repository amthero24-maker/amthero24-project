"""WhatsApp and operator API tests for human-support handoff."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

import support_extensions
from data_store import JsonDataStore


def _install_store(tmp_path, monkeypatch) -> JsonDataStore:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    support_extensions.core.store = store
    support_extensions.core._hero_memory_store = support_extensions.core.HeroMemory(store)
    support_extensions._SUPPORT_REPOSITORY = None
    return store


def _enable(monkeypatch) -> None:
    monkeypatch.setenv("HUMAN_SUPPORT_ENABLED", "true")
    monkeypatch.setenv("SUPPORT_API_TOKEN", "support-secret")
    monkeypatch.setenv("SUPPORT_ENCRYPTION_KEY", "encryption-secret")


def _seed_user(store: JsonDataStore) -> None:
    store.update_user("49123", {
        "memory_consent": "granted",
        "memory_consent_at": datetime.now(UTC).isoformat(),
        "memory_consent_version": "test-v1",
        "onboarding_stage": "complete",
        "intro_sent_at": datetime.now(UTC).isoformat(),
        "preferred_language": "ar",
        "first_name": "وسام",
    })


@pytest.mark.anyio
async def test_disabled_support_is_truthful_and_does_not_create_ticket(tmp_path, monkeypatch) -> None:
    store = _install_store(tmp_path, monkeypatch)
    monkeypatch.setenv("HUMAN_SUPPORT_ENABLED", "false")
    _seed_user(store)
    message = support_extensions.core.IncomingMessage("support-off", "49123", "بدي احكي مع شخص", "text")
    store.claim_message("support-off", "49123", message.text)

    with patch.object(support_extensions.core, "send_whatsapp_message", new=AsyncMock()) as send, patch.object(
        support_extensions, "_ORIGINAL_PROCESS_INCOMING", new=AsyncMock()
    ) as original:
        await support_extensions.process_incoming(message)

    original.assert_not_awaited()
    assert "مو مفعّل حاليًا" in send.await_args.args[1]
    assert store.snapshot().get("support_tickets", {}) == {}


@pytest.mark.anyio
async def test_explicit_request_creates_minimal_ticket_and_status_cancel_work(tmp_path, monkeypatch) -> None:
    store = _install_store(tmp_path, monkeypatch)
    _enable(monkeypatch)
    _seed_user(store)

    with patch.object(support_extensions.core, "send_whatsapp_message", new=AsyncMock()) as send:
        create = support_extensions.core.IncomingMessage("support-create", "49123", "بدي احكي مع شخص عن مشكلة تقنية عاجلة اليوم", "text")
        store.claim_message(create.message_id, create.sender, create.text)
        await support_extensions.process_incoming(create)
        assert "تم ✅" in send.await_args.args[1]

        status = support_extensions.core.IncomingMessage("support-status", "49123", "حالة طلب الدعم", "text")
        store.claim_message(status.message_id, status.sender, status.text)
        await support_extensions.process_incoming(status)
        assert "بانتظار المراجعة" in send.await_args.args[1]

        cancel = support_extensions.core.IncomingMessage("support-cancel", "49123", "الغي طلب الدعم", "text")
        store.claim_message(cancel.message_id, cancel.sender, cancel.text)
        await support_extensions.process_incoming(cancel)
        assert "تم إلغاء" in send.await_args.args[1]

    snapshot = store.snapshot()
    ticket = next(iter(snapshot["support_tickets"].values()))
    assert ticket["category"] == "technical"
    assert ticket["urgency"] == "high"
    assert "مشكلة تقنية عاجلة" not in str(ticket)


def test_support_operator_endpoint_is_separately_protected_and_minimal(tmp_path, monkeypatch) -> None:
    _install_store(tmp_path, monkeypatch)
    _enable(monkeypatch)
    repository = support_extensions._repository()
    ticket = repository.create("49123", language="ar", category="document", urgency="high")
    client = TestClient(support_extensions.core.app)

    response = client.get("/admin/support/tickets", headers={"X-Support-Token": "wrong"})
    assert response.status_code == 401

    response = client.get("/admin/support/tickets", headers={"X-Support-Token": "support-secret"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    item = response.json()["tickets"][0]
    assert item["contact"] == "49123"
    assert item["category"] == "document"
    assert "contact_ciphertext" not in response.text
    assert "phone_hash" not in response.text

    response = client.post(
        f"/admin/support/tickets/{ticket['ticket_id']}/status",
        headers={"X-Support-Token": "support-secret"},
        json={"status": "resolved"},
    )
    assert response.status_code == 200
    assert response.json()["ticket"]["status"] == "resolved"
    assert "contact" not in response.json()["ticket"]


def test_privacy_delete_removes_support_ticket(tmp_path, monkeypatch) -> None:
    _install_store(tmp_path, monkeypatch)
    _enable(monkeypatch)
    support_extensions._repository().create("49123", language="en", category="general", urgency="normal")
    with patch.object(support_extensions, "_ORIGINAL_PRIVACY_DELETE", return_value=False):
        assert support_extensions._privacy_delete(support_extensions.core.store, "49123") is True
    assert support_extensions._repository().latest_for_user("49123") is None
