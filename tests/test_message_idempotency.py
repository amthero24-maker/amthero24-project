"""Retry-safe message claim tests over the atomic JSON store."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from data_store import JsonDataStore
from message_idempotency import MessageClaimRepository


def _repository(tmp_path, monkeypatch, *, lease: timedelta = timedelta(minutes=10)):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "idempotency.json")
    return store, MessageClaimRepository(store, lease=lease)


def test_new_claim_is_exclusive_and_does_not_store_raw_contact_or_media_id(tmp_path, monkeypatch) -> None:
    store, repository = _repository(tmp_path, monkeypatch)
    phone = "+491701234567"
    now = datetime(2026, 8, 6, 9, 0, tzinfo=UTC)

    assert repository.claim("wamid.one", phone, "hello", media_id="private-media-id", now=now) is True
    assert repository.claim("wamid.one", phone, "hello", media_id="private-media-id", now=now) is False

    record = store.snapshot()["messages"]["wamid.one"]
    assert record["phone_hash"] != phone
    assert phone not in str(record)
    assert "media_id" not in record
    assert record["has_media"] is True
    assert record["status"] == "processing"
    assert record["attempt_count"] == 1


def test_failed_claim_retries_but_sent_claim_is_terminal(tmp_path, monkeypatch) -> None:
    store, repository = _repository(tmp_path, monkeypatch)
    now = datetime(2026, 8, 6, 10, 0, tzinfo=UTC)

    assert repository.claim("wamid.retry", "49170", "retry", now=now)
    store.update_message_status("wamid.retry", "failed")
    assert repository.claim("wamid.retry", "49170", "retry", now=now + timedelta(seconds=1))
    assert repository.state("wamid.retry")["attempt_count"] == 2

    store.update_message_status("wamid.retry", "sent")
    assert repository.claim("wamid.retry", "49170", "retry", now=now + timedelta(minutes=30)) is False
    assert repository.state("wamid.retry")["status"] == "sent"


def test_abandoned_processing_lease_can_be_reclaimed(tmp_path, monkeypatch) -> None:
    _, repository = _repository(tmp_path, monkeypatch, lease=timedelta(minutes=2))
    now = datetime(2026, 8, 6, 11, 0, tzinfo=UTC)

    assert repository.claim("wamid.stale", "49171", now=now)
    assert repository.claim("wamid.stale", "49171", now=now + timedelta(minutes=1)) is False
    assert repository.claim("wamid.stale", "49171", now=now + timedelta(minutes=3)) is True
    assert repository.state("wamid.stale")["attempt_count"] == 2


def test_same_message_id_cannot_move_between_senders(tmp_path, monkeypatch) -> None:
    _, repository = _repository(tmp_path, monkeypatch)
    now = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)

    assert repository.claim("wamid.bound", "+491700001", now=now)
    assert repository.claim("wamid.bound", "+491700002", now=now + timedelta(hours=1)) is False


def test_concurrent_claims_choose_exactly_one_worker(tmp_path, monkeypatch) -> None:
    _, repository = _repository(tmp_path, monkeypatch)
    now = datetime(2026, 8, 6, 13, 0, tzinfo=UTC)

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(
            lambda _: repository.claim("wamid.concurrent", "+491709999", "hello", now=now),
            range(24),
        ))

    assert results.count(True) == 1
    assert results.count(False) == 23
