"""Composition tests for graceful runtime drain behavior."""
from __future__ import annotations

import asyncio
import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("GROQ_API_KEY", "isolated-groq-key")
os.environ.setdefault("WHATSAPP_TOKEN", "isolated-whatsapp-token")
os.environ.setdefault("PHONE_NUMBER_ID", "isolated-phone-id")
os.environ.setdefault("VERIFY_TOKEN", "isolated-verify-token")
os.environ.setdefault("REMINDER_ENCRYPTION_KEY", "isolated-reminder-key-2026-safe")
os.environ.setdefault("SUPPORT_ENCRYPTION_KEY", "isolated-support-key-2026-safe")

import graceful_shutdown_extensions as graceful
import idempotency_extensions as idempotency
import runtime_health
from data_store import JsonDataStore
from runtime_lifecycle import lifecycle


@pytest.fixture(autouse=True)
def reset_lifecycle_and_store():
    previous_store = idempotency.core.store
    lifecycle.reset_accepting()
    yield
    idempotency.core.store = previous_store
    idempotency._MESSAGE_REPOSITORY = None
    lifecycle.reset_accepting()


def test_final_composition_orders_drain_before_worker_shutdown() -> None:
    handlers = graceful.core.app.router.on_shutdown
    assert handlers[0] is graceful._begin_drain
    assert handlers[-1] is graceful._finish_drain
    bounded_names = [getattr(handler, "__name__", "") for handler in handlers[1:-1]]
    assert any("stop_worker" in name for name in bounded_names)


def test_draining_readiness_fails_without_exposing_active_details(tmp_path, monkeypatch) -> None:
    class Store:
        backend_name = "json"
        path = tmp_path / "store.json"

    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setenv("WHATSAPP_TOKEN", "x")
    monkeypatch.setenv("PHONE_NUMBER_ID", "x")
    monkeypatch.setenv("VERIFY_TOKEN", "x")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    lifecycle.begin_draining()

    payload, status = runtime_health.readiness_payload(Store(), version="test", model="test")

    assert status == 503
    assert payload["status"] == "not_ready"
    assert payload["components"]["runtime_lifecycle"] == "draining"
    assert "active_work" not in payload["components"]


def test_webhook_returns_retryable_503_as_soon_as_drain_starts(monkeypatch) -> None:
    monkeypatch.setenv("DURABLE_QUEUE_ENABLED", "false")
    lifecycle.begin_draining()

    response = TestClient(graceful.app).post(
        "/webhook",
        json={"entry": [{"changes": [{"value": {"messages": []}}]}]},
    )

    assert response.status_code == 503
    assert response.json() == {"status": "draining"}
    assert response.headers["retry-after"] == "10"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.anyio
async def test_non_durable_background_work_releases_claim_when_drain_wins_race(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "non-durable-drain.json")
    idempotency.core.store = store
    idempotency._MESSAGE_REPOSITORY = None
    repository = idempotency._repository(store)
    message = idempotency.core.IncomingMessage(
        "wamid.drain-race",
        "+491700000111",
        "private request",
        "text",
    )
    assert repository.claim(message.message_id, message.sender, message.text)
    lifecycle.begin_draining()

    await idempotency._process_claimed(message)

    assert repository.state(message.message_id)["status"] == "failed"
    assert lifecycle.snapshot().active_work == 0


@pytest.mark.anyio
async def test_bounded_shutdown_cancels_stuck_handler_with_shared_budget(monkeypatch) -> None:
    lifecycle.begin_draining()
    monkeypatch.setattr(lifecycle, "remaining_grace_seconds", lambda: 0.0)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def stuck_handler() -> None:
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    await graceful._bounded(stuck_handler)()

    assert started.is_set()
    assert cancelled.is_set()
