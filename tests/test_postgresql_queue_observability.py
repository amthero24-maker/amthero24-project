"""Real PostgreSQL tests for aggregate queue health and retention operations."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

import runtime_health
import admin_extensions as admin_module
import privacy_engine as privacy_module
from admin_metrics import contains_personal_fields
from durable_queue import DurableQueueRepository
from message_idempotency import MessageClaimRepository
from queue_observability import build_queue_overview


@pytest.fixture(autouse=True)
def clean_queue(monkeypatch) -> None:
    monkeypatch.setenv("DURABLE_QUEUE_ENABLED", "true")
    monkeypatch.setenv("MESSAGE_QUEUE_ENCRYPTION_KEY", "queue-observability-ci-2026-unique-8mQ4xT7pL2vN")
    monkeypatch.setenv("DURABLE_QUEUE_COMPLETED_RETENTION_HOURS", "1")
    store = runtime_health.store
    assert store.backend_name == "postgresql"
    with store.pool.connection() as connection:
        connection.execute("TRUNCATE inbound_work_queue, inbound_messages")
    yield
    with store.pool.connection() as connection:
        connection.execute("TRUNCATE inbound_work_queue, inbound_messages")


def _seed(message_id: str, phone: str, marker: str, *, now: datetime) -> DurableQueueRepository:
    store = runtime_health.store
    claims = MessageClaimRepository(store)
    assert claims.claim(message_id, phone, marker, now=now)
    queue = DurableQueueRepository(store)
    queue.enqueue(message_id, phone, now=now)
    return queue


def test_postgres_queue_overview_reports_counts_and_ages_without_identifiers() -> None:
    store = runtime_health.store
    current = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
    markers = {
        "ready": ("wamid.metrics-ready", "+491707770001", "PRIVATE_READY_TEXT"),
        "delayed": ("wamid.metrics-delayed", "+491707770002", "PRIVATE_DELAYED_TEXT"),
        "stale": ("wamid.metrics-stale", "+491707770003", "PRIVATE_STALE_TEXT"),
        "active": ("wamid.metrics-active", "+491707770004", "PRIVATE_ACTIVE_TEXT"),
        "dead": ("wamid.metrics-dead", "+491707770005", "PRIVATE_DEAD_TEXT"),
        "completed": ("wamid.metrics-completed", "+491707770006", "PRIVATE_COMPLETED_TEXT"),
    }
    for message_id, phone, marker in markers.values():
        _seed(message_id, phone, marker, now=current)

    with store.pool.connection() as connection:
        connection.execute(
            """
            UPDATE inbound_work_queue
            SET status = 'queued', available_at = %s, attempt_count = 3, updated_at = %s
            WHERE message_id = %s
            """,
            (current - timedelta(minutes=10), current - timedelta(minutes=10), markers["ready"][0]),
        )
        connection.execute(
            """
            UPDATE inbound_work_queue
            SET status = 'queued', available_at = %s, attempt_count = 1, updated_at = %s
            WHERE message_id = %s
            """,
            (current + timedelta(minutes=5), current, markers["delayed"][0]),
        )
        connection.execute(
            """
            UPDATE inbound_work_queue
            SET status = 'processing', lease_until = %s, attempt_count = 2, updated_at = %s
            WHERE message_id = %s
            """,
            (current - timedelta(minutes=2), current - timedelta(minutes=2), markers["stale"][0]),
        )
        connection.execute(
            """
            UPDATE inbound_work_queue
            SET status = 'processing', lease_until = %s, attempt_count = 1, updated_at = %s
            WHERE message_id = %s
            """,
            (current + timedelta(minutes=10), current, markers["active"][0]),
        )
        connection.execute(
            """
            UPDATE inbound_work_queue
            SET status = 'dead', sender_ciphertext = '', media_id_ciphertext = NULL,
                lease_until = NULL, attempt_count = 5, updated_at = %s
            WHERE message_id = %s
            """,
            (current - timedelta(minutes=1), markers["dead"][0]),
        )
        connection.execute(
            """
            UPDATE inbound_work_queue
            SET status = 'completed', sender_ciphertext = '', media_id_ciphertext = NULL,
                lease_until = NULL, attempt_count = 1, updated_at = %s
            WHERE message_id = %s
            """,
            (current, markers["completed"][0]),
        )

    overview = build_queue_overview(store, now=current)

    assert overview == {
        "mode": "configured",
        "total": 6,
        "by_status": {"queued": 2, "processing": 2, "completed": 1, "dead": 1},
        "ready": 1,
        "delayed": 1,
        "stale_processing": 1,
        "retrying": 2,
        "dead_24h": 1,
        "oldest_ready_age_seconds": 600,
        "max_attempt_count": 5,
    }
    encoded = json.dumps(overview, ensure_ascii=False, sort_keys=True)
    for message_id, phone, marker in markers.values():
        assert message_id not in encoded
        assert phone not in encoded
        assert marker not in encoded
    assert contains_personal_fields({"durable_queue": overview}) is False


def test_admin_overview_includes_only_aggregate_queue_health() -> None:
    store = runtime_health.store
    current = datetime(2026, 8, 9, 13, 0, tzinfo=UTC)
    _seed("wamid.admin-aggregate", "+491707771111", "ADMIN_PRIVATE_TEXT", now=current)

    payload = admin_module.build_overview(
        store,
        now=current,
        version="test-version",
        model="test-model",
    )

    assert payload["durable_queue"]["total"] == 1
    assert payload["durable_queue"]["ready"] == 1
    assert contains_personal_fields(payload) is False
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    assert "wamid.admin-aggregate" not in encoded
    assert "+491707771111" not in encoded
    assert "ADMIN_PRIVATE_TEXT" not in encoded


def test_privacy_retention_removes_expired_and_old_terminal_queue_rows() -> None:
    store = runtime_health.store
    current = datetime(2026, 8, 9, 14, 0, tzinfo=UTC)
    completed_id = "wamid.cleanup-completed"
    expired_id = "wamid.cleanup-expired"
    _seed(completed_id, "+491707772222", "CLEANUP_COMPLETED", now=current)
    _seed(expired_id, "+491707773333", "CLEANUP_EXPIRED", now=current)

    with store.pool.connection() as connection:
        connection.execute(
            """
            UPDATE inbound_work_queue
            SET status = 'completed', sender_ciphertext = '', media_id_ciphertext = NULL,
                updated_at = %s, expires_at = %s
            WHERE message_id = %s
            """,
            (current - timedelta(hours=2), current + timedelta(hours=12), completed_id),
        )
        connection.execute(
            """
            UPDATE inbound_work_queue
            SET expires_at = %s, updated_at = %s
            WHERE message_id = %s
            """,
            (current - timedelta(seconds=1), current, expired_id),
        )

    result = privacy_module.cleanup_retention(store, now=current, message_hours=24)

    assert result["durable_queue"] == 2
    with store.pool.connection() as connection:
        queue_count = connection.execute(
            "SELECT COUNT(*) AS count FROM inbound_work_queue"
        ).fetchone()
        message_count = connection.execute(
            "SELECT COUNT(*) AS count FROM inbound_messages"
        ).fetchone()
    assert int(queue_count["count"]) == 0
    assert int(message_count["count"]) == 2
