"""Real PostgreSQL tests for graceful durable-work interruption and recovery."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

import durable_queue_extensions as layer
import runtime_health
from durable_queue import DurableQueueRepository
from message_idempotency import MessageClaimRepository
from queue_drain import release_processing_item
from runtime_lifecycle import lifecycle


@pytest.fixture(autouse=True)
def clean_drain_state(monkeypatch) -> None:
    monkeypatch.setenv("DURABLE_QUEUE_ENABLED", "true")
    monkeypatch.setenv("MESSAGE_QUEUE_ENCRYPTION_KEY", "queue-ci-2026-unique-7rT4mQ9xLp2V8nK5")
    monkeypatch.setenv("SHUTDOWN_RETRY_DELAY_SECONDS", "30")
    store = runtime_health.store
    assert store.backend_name == "postgresql"
    with store.pool.connection() as connection:
        connection.execute("TRUNCATE inbound_work_queue, inbound_messages")
    layer._QUEUE_REPOSITORY = None
    lifecycle.reset_accepting()
    yield
    lifecycle.reset_accepting()
    with store.pool.connection() as connection:
        connection.execute("TRUNCATE inbound_work_queue, inbound_messages")
    layer._QUEUE_REPOSITORY = None


def _seed(message_id: str, phone: str, *, now: datetime) -> DurableQueueRepository:
    store = runtime_health.store
    claims = MessageClaimRepository(store)
    assert claims.claim(message_id, phone, "PRIVATE_DRAIN_MESSAGE", now=now)
    queue = DurableQueueRepository(store)
    queue.enqueue(message_id, phone, now=now)
    return queue


def test_release_returns_processing_item_after_safe_delay_without_decrypting() -> None:
    store = runtime_health.store
    now = datetime(2026, 8, 15, 9, 0, tzinfo=UTC)
    phone = "+491708880111"
    queue = _seed("wamid.drain-release", phone, now=now)
    assert queue.claim("wamid.drain-release", now=now) is not None

    assert release_processing_item(
        store,
        "wamid.drain-release",
        delay_seconds=30,
        now=now + timedelta(seconds=2),
    ) is True

    with store.pool.connection() as connection:
        row = connection.execute(
            """
            SELECT status, available_at, lease_until, last_failure_code,
                   sender_ciphertext, media_id_ciphertext
            FROM inbound_work_queue WHERE message_id = %s
            """,
            ("wamid.drain-release",),
        ).fetchone()
    assert row["status"] == "queued"
    assert row["available_at"] == now + timedelta(seconds=32)
    assert row["lease_until"] is None
    assert row["last_failure_code"] == "shutdown_interrupted"
    assert row["sender_ciphertext"]
    assert phone not in row["sender_ciphertext"]
    assert row["media_id_ciphertext"] is None

    assert queue.claim("wamid.drain-release", now=now + timedelta(seconds=31)) is None
    recovered = queue.claim("wamid.drain-release", now=now + timedelta(seconds=32))
    assert recovered is not None
    assert recovered.sender == phone
    assert recovered.attempt_count == 2


def test_release_is_scoped_to_processing_rows_only() -> None:
    store = runtime_health.store
    now = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
    _seed("wamid.drain-queued", "+491708880222", now=now)

    assert release_processing_item(store, "wamid.drain-queued", now=now) is False
    assert release_processing_item(store, "wamid.missing", now=now) is False

    with store.pool.connection() as connection:
        row = connection.execute(
            "SELECT status, last_failure_code FROM inbound_work_queue WHERE message_id = %s",
            ("wamid.drain-queued",),
        ).fetchone()
    assert row["status"] == "queued"
    assert row["last_failure_code"] == ""


@pytest.mark.anyio
async def test_cancelling_active_processing_releases_lease_and_finishes_counter(monkeypatch) -> None:
    store = runtime_health.store
    now = datetime.now(UTC) - timedelta(seconds=1)
    phone = "+491708880333"
    queue = _seed("wamid.drain-cancel", phone, now=now)
    layer._QUEUE_REPOSITORY = queue
    started = asyncio.Event()

    async def process(_message) -> None:
        started.set()
        await asyncio.sleep(60)

    monkeypatch.setattr(layer.core, "process_incoming", process)
    task = asyncio.create_task(layer._process_queue_message("wamid.drain-cancel"))
    await asyncio.wait_for(started.wait(), timeout=2)
    assert lifecycle.snapshot().active_work == 1

    lifecycle.begin_draining()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with store.pool.connection() as connection:
        row = connection.execute(
            """
            SELECT status, lease_until, last_failure_code, available_at, sender_ciphertext
            FROM inbound_work_queue WHERE message_id = %s
            """,
            ("wamid.drain-cancel",),
        ).fetchone()
    assert row["status"] == "queued"
    assert row["lease_until"] is None
    assert row["last_failure_code"] == "shutdown_interrupted"
    assert row["available_at"] > datetime.now(UTC)
    assert row["sender_ciphertext"]
    assert phone not in row["sender_ciphertext"]
    assert lifecycle.snapshot().active_work == 0


@pytest.mark.anyio
async def test_terminal_message_is_completed_not_requeued_when_cancelled(monkeypatch) -> None:
    store = runtime_health.store
    now = datetime.now(UTC) - timedelta(seconds=1)
    queue = _seed("wamid.drain-terminal", "+491708880444", now=now)
    layer._QUEUE_REPOSITORY = queue
    sent = asyncio.Event()

    async def process(message) -> None:
        store.update_message_status(message.message_id, "sent")
        sent.set()
        await asyncio.sleep(60)

    monkeypatch.setattr(layer.core, "process_incoming", process)
    task = asyncio.create_task(layer._process_queue_message("wamid.drain-terminal"))
    await asyncio.wait_for(sent.wait(), timeout=2)
    lifecycle.begin_draining()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with store.pool.connection() as connection:
        row = connection.execute(
            """
            SELECT status, sender_ciphertext, media_id_ciphertext, lease_until
            FROM inbound_work_queue WHERE message_id = %s
            """,
            ("wamid.drain-terminal",),
        ).fetchone()
    assert row["status"] == "completed"
    assert row["sender_ciphertext"] == ""
    assert row["media_id_ciphertext"] is None
    assert row["lease_until"] is None
