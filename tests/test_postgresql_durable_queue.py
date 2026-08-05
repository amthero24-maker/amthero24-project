"""Real PostgreSQL crash-recovery tests for durable workers and graceful drain."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

import durable_queue_extensions as layer
import reminder_extensions as reminder_layer
import runtime_health
from durable_queue import DurableQueueRepository
from message_idempotency import MessageClaimRepository


@pytest.fixture(autouse=True)
def clean_queue(monkeypatch) -> None:
    monkeypatch.setenv("DURABLE_QUEUE_ENABLED", "true")
    monkeypatch.setenv("MESSAGE_QUEUE_ENCRYPTION_KEY", "queue-ci-2026-unique-7rT4mQ9xLp2V8nK5")
    layer.lifecycle.start_accepting()
    store = runtime_health.store
    assert store.backend_name == "postgresql"
    with store.pool.connection() as connection:
        connection.execute("TRUNCATE inbound_work_queue, inbound_messages, hero_reminders")
    layer._QUEUE_REPOSITORY = None
    reminder_layer._REMINDER_REPOSITORY = None
    yield
    with store.pool.connection() as connection:
        connection.execute("TRUNCATE inbound_work_queue, inbound_messages, hero_reminders")
    layer._QUEUE_REPOSITORY = None
    reminder_layer._REMINDER_REPOSITORY = None


def _seed(message_id: str, phone: str, *, media_id: str | None = None, now: datetime) -> DurableQueueRepository:
    store = runtime_health.store
    claims = MessageClaimRepository(store)
    assert claims.claim(
        message_id,
        phone,
        "QUEUE_MESSAGE",
        message_type="image" if media_id else "text",
        media_id=media_id,
        now=now,
    )
    queue = DurableQueueRepository(store)
    queue.enqueue(
        message_id,
        phone,
        media_id=media_id,
        mime_type="image/jpeg" if media_id else "application/octet-stream",
        now=now,
    )
    return queue


def test_queue_envelope_is_encrypted_and_exactly_one_replica_claims_it() -> None:
    store = runtime_health.store
    now = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)
    phone = "+491706661111"
    media_id = "meta-media-private-123"
    _seed("wamid.queue-one", phone, media_id=media_id, now=now)
    first = DurableQueueRepository(store)
    second = DurableQueueRepository(store)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(
            lambda repository: repository.claim("wamid.queue-one", now=now),
            [first, second] * 4,
        ))

    winners = [item for item in results if item is not None]
    assert len(winners) == 1
    item = winners[0]
    assert item.sender == phone
    assert item.media_id == media_id
    assert item.text == "QUEUE_MESSAGE"
    assert item.message_type == "image"
    assert item.mime_type == "image/jpeg"
    assert item.attempt_count == 1

    with store.pool.connection() as connection:
        row = connection.execute(
            """
            SELECT sender_ciphertext, media_id_ciphertext, status
            FROM inbound_work_queue WHERE message_id = 'wamid.queue-one'
            """
        ).fetchone()
    assert row["sender_ciphertext"] != phone
    assert phone not in row["sender_ciphertext"]
    assert row["media_id_ciphertext"] != media_id
    assert media_id not in row["media_id_ciphertext"]
    assert row["status"] == "processing"

    first.complete("wamid.queue-one", now=now + timedelta(minutes=1))
    with store.pool.connection() as connection:
        erased = connection.execute(
            """
            SELECT sender_ciphertext, media_id_ciphertext, status
            FROM inbound_work_queue WHERE message_id = 'wamid.queue-one'
            """
        ).fetchone()
    assert erased["status"] == "completed"
    assert erased["sender_ciphertext"] == ""
    assert erased["media_id_ciphertext"] is None


def test_expired_worker_lease_is_recovered_after_restart() -> None:
    now = datetime(2026, 8, 8, 10, 0, tzinfo=UTC)
    queue = _seed("wamid.queue-stale", "+491706662222", now=now)
    assert queue.claim("wamid.queue-stale", now=now) is not None
    assert queue.claim("wamid.queue-stale", now=now + timedelta(minutes=10)) is None
    recovered = queue.claim("wamid.queue-stale", now=now + timedelta(minutes=16))
    assert recovered is not None
    assert recovered.attempt_count == 2


def test_graceful_drain_releases_only_current_process_owned_lease() -> None:
    store = runtime_health.store
    now = datetime(2026, 8, 8, 11, 0, tzinfo=UTC)
    _seed("wamid.owned", "+491706663331", now=now)
    _seed("wamid.other", "+491706663332", now=now)
    owned = layer.OwnedDurableQueueRepository(store)

    assert owned.claim("wamid.owned", now=now) is not None
    assert owned.claim("wamid.other", now=now) is not None
    with store.pool.connection() as connection:
        connection.execute(
            "UPDATE inbound_work_queue SET lease_owner = 'different-process' WHERE message_id = %s",
            ("wamid.other",),
        )

    assert owned.release_owned(now=now + timedelta(seconds=1)) == 1
    with store.pool.connection() as connection:
        rows = connection.execute(
            "SELECT message_id, status, lease_owner FROM inbound_work_queue ORDER BY message_id"
        ).fetchall()
    states = {row["message_id"]: dict(row) for row in rows}
    assert states["wamid.owned"]["status"] == "queued"
    assert states["wamid.owned"]["lease_owner"] is None
    assert states["wamid.other"]["status"] == "processing"
    assert states["wamid.other"]["lease_owner"] == "different-process"


def test_graceful_drain_releases_only_current_process_reminder_lease() -> None:
    store = runtime_health.store
    now = datetime(2026, 8, 8, 11, 30, tzinfo=UTC)
    repository = reminder_layer.ResilientReminderRepository(store)
    first = repository.create(
        "+491706663341",
        title="Owned reminder",
        scheduled_at=now - timedelta(minutes=1),
        language="de",
    )
    second = repository.create(
        "+491706663342",
        title="Other reminder",
        scheduled_at=now - timedelta(minutes=1),
        language="de",
    )
    claimed = repository.claim_due(now=now, limit=10)
    assert {item["reminder_id"] for item in claimed} == {first["reminder_id"], second["reminder_id"]}
    with store.pool.connection() as connection:
        connection.execute(
            "UPDATE hero_reminders SET lease_owner = 'different-process' WHERE reminder_id = %s",
            (second["reminder_id"],),
        )

    assert repository.release_owned(now=now + timedelta(seconds=1)) == 1
    with store.pool.connection() as connection:
        rows = connection.execute(
            "SELECT reminder_id, status, lease_owner, last_error FROM hero_reminders ORDER BY reminder_id"
        ).fetchall()
    states = {row["reminder_id"]: dict(row) for row in rows}
    assert states[first["reminder_id"]]["status"] == "failed"
    assert states[first["reminder_id"]]["lease_owner"] is None
    assert states[first["reminder_id"]]["last_error"] == "process_draining"
    assert states[second["reminder_id"]]["status"] == "processing"
    assert states[second["reminder_id"]]["lease_owner"] == "different-process"


def test_reminder_canary_claims_only_allowlisted_recipient(monkeypatch) -> None:
    store = runtime_health.store
    now = datetime(2026, 8, 8, 11, 45, tzinfo=UTC)
    allowed_phone = "+491706663351"
    blocked_phone = "+491706663352"
    monkeypatch.setenv("REMINDER_WORKER_ENABLED", "true")
    monkeypatch.setenv("REMINDER_CANARY_SENDERS", allowed_phone)
    repository = reminder_layer.ResilientReminderRepository(store)
    allowed = repository.create(
        allowed_phone,
        title="Allowed reminder",
        scheduled_at=now - timedelta(minutes=1),
        language="de",
    )
    repository.create(
        blocked_phone,
        title="Blocked reminder",
        scheduled_at=now - timedelta(minutes=1),
        language="de",
    )

    claimed = repository.claim_due(now=now, limit=10)

    assert [item["reminder_id"] for item in claimed] == [allowed["reminder_id"]]
    assert repository.list(blocked_phone)[0]["status"] == "pending"


@pytest.mark.anyio
async def test_recovery_worker_processes_persisted_envelope_after_restart(monkeypatch) -> None:
    store = runtime_health.store
    now = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    queue = _seed("wamid.queue-recover", "+491706664444", now=now)
    layer._QUEUE_REPOSITORY = queue

    async def process(message) -> None:
        assert message.message_id == "wamid.queue-recover"
        assert message.sender == "+491706664444"
        store.update_message_status(message.message_id, "sent")

    monkeypatch.setattr(layer.core, "process_incoming", process)
    assert await layer._process_queue_message("wamid.queue-recover") is True

    with store.pool.connection() as connection:
        row = connection.execute(
            "SELECT status, sender_ciphertext FROM inbound_work_queue WHERE message_id = %s",
            ("wamid.queue-recover",),
        ).fetchone()
    assert row["status"] == "completed"
    assert row["sender_ciphertext"] == ""


@pytest.mark.anyio
async def test_already_sent_recovery_is_completed_without_second_processing(monkeypatch) -> None:
    store = runtime_health.store
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    queue = _seed("wamid.queue-sent", "+491706665555", now=now)
    store.update_message_status("wamid.queue-sent", "sent")
    layer._QUEUE_REPOSITORY = queue
    process = AsyncMock(side_effect=AssertionError("sent message must not be processed twice"))
    monkeypatch.setattr(layer.core, "process_incoming", process)

    assert await layer._process_queue_message("wamid.queue-sent") is True
    process.assert_not_awaited()
    with store.pool.connection() as connection:
        row = connection.execute(
            "SELECT status, sender_ciphertext FROM inbound_work_queue WHERE message_id = %s",
            ("wamid.queue-sent",),
        ).fetchone()
    assert row["status"] == "completed"
    assert row["sender_ciphertext"] == ""
