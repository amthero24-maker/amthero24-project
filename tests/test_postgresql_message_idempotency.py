"""Real PostgreSQL tests for cross-replica message idempotency and retry leases."""
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

import runtime_health
from message_idempotency import MessageClaimRepository


@pytest.fixture(autouse=True)
def clean_messages() -> None:
    store = runtime_health.store
    assert store.backend_name == "postgresql"
    with store.pool.connection() as connection:
        connection.execute("TRUNCATE inbound_messages")
    yield
    with store.pool.connection() as connection:
        connection.execute("TRUNCATE inbound_messages")


def test_postgres_claim_schema_and_cross_replica_exclusivity() -> None:
    store = runtime_health.store
    first = MessageClaimRepository(store)
    second = MessageClaimRepository(store)
    now = datetime(2026, 8, 7, 9, 0, tzinfo=UTC)

    with ThreadPoolExecutor(max_workers=12) as executor:
        repositories = [first, second] * 12
        results = list(executor.map(
            lambda repository: repository.claim(
                "wamid.pg-concurrent",
                "+491701111111",
                "PG_MESSAGE",
                now=now,
            ),
            repositories,
        ))

    assert results.count(True) == 1
    assert results.count(False) == 23

    with store.pool.connection() as connection:
        columns = connection.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = 'inbound_messages'
            """
        ).fetchall()
        row = connection.execute(
            """
            SELECT phone_hash, text, status, attempt_count, lease_until
            FROM inbound_messages WHERE message_id = 'wamid.pg-concurrent'
            """
        ).fetchone()

    assert {item["column_name"] for item in columns} >= {"lease_until", "attempt_count"}
    assert row["phone_hash"] == hashlib.sha256("+491701111111".encode()).hexdigest()
    assert row["phone_hash"] != "+491701111111"
    assert row["text"] == "PG_MESSAGE"
    assert row["status"] == "processing"
    assert int(row["attempt_count"]) == 1
    assert row["lease_until"] is not None


def test_failed_and_abandoned_claims_retry_while_sent_is_terminal() -> None:
    store = runtime_health.store
    repository = MessageClaimRepository(store, lease=timedelta(minutes=2))
    now = datetime(2026, 8, 7, 10, 0, tzinfo=UTC)

    assert repository.claim("wamid.pg-retry", "+491702222222", now=now)
    store.update_message_status("wamid.pg-retry", "failed")
    assert repository.claim("wamid.pg-retry", "+491702222222", now=now + timedelta(seconds=1))
    assert repository.state("wamid.pg-retry")["attempt_count"] == 2
    store.update_message_status("wamid.pg-retry", "sent")
    assert repository.claim("wamid.pg-retry", "+491702222222", now=now + timedelta(hours=1)) is False

    assert repository.claim("wamid.pg-stale", "+491703333333", now=now)
    assert repository.claim("wamid.pg-stale", "+491703333333", now=now + timedelta(minutes=1)) is False
    assert repository.claim("wamid.pg-stale", "+491703333333", now=now + timedelta(minutes=3)) is True
    assert repository.state("wamid.pg-stale")["attempt_count"] == 2


def test_same_message_id_cannot_be_reassigned_to_another_tenant() -> None:
    repository = MessageClaimRepository(runtime_health.store, lease=timedelta(minutes=1))
    now = datetime(2026, 8, 7, 11, 0, tzinfo=UTC)

    assert repository.claim("wamid.pg-bound", "+491704444444", now=now)
    assert repository.claim("wamid.pg-bound", "+491705555555", now=now + timedelta(minutes=2)) is False
