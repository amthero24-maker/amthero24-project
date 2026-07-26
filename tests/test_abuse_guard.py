"""Sender burst protection, privacy, cleanup, and metrics tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from abuse_guard import AbuseGuardRepository
from data_store import JsonDataStore


def _repository(tmp_path, monkeypatch) -> AbuseGuardRepository:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ABUSE_GUARD_ENABLED", "true")
    monkeypatch.setenv("ABUSE_MESSAGES_PER_MINUTE", "5")
    monkeypatch.setenv("ABUSE_MESSAGES_PER_HOUR", "100")
    monkeypatch.setenv("ABUSE_MEDIA_PER_HOUR", "100")
    monkeypatch.setenv("ABUSE_COOLDOWN_SECONDS", "60")
    monkeypatch.setenv("ABUSE_NOTICE_INTERVAL_SECONDS", "60")
    return AbuseGuardRepository(JsonDataStore(tmp_path / "store.json"))


def test_burst_is_blocked_and_notice_is_not_repeated(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ABUSE_GUARD_ENFORCEMENT_ENABLED", "true")
    repository = _repository(tmp_path, monkeypatch)
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)

    for _ in range(5):
        assert repository.check("49123", now=now).allowed is True
    blocked = repository.check("49123", now=now)
    repeated = repository.check("49123", now=now + timedelta(seconds=1))

    assert blocked.allowed is False
    assert blocked.notify is True
    assert blocked.reason == "minute_burst"
    assert repeated.allowed is False
    assert repeated.notify is False
    assert repository.aggregate(now=now)["active_blocks"] == 1


def test_observe_only_records_limit_event_without_blocking(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ABUSE_GUARD_ENFORCEMENT_ENABLED", "false")
    repository = _repository(tmp_path, monkeypatch)
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)

    decision = None
    for _ in range(6):
        decision = repository.check("49123", now=now)

    assert decision is not None and decision.allowed is True
    assert decision.reason == "minute_burst"
    metrics = repository.aggregate(now=now)
    assert metrics["events_24h"] == {"limit_observed": 1}
    assert metrics["active_blocks"] == 0


def test_media_volume_has_separate_limit(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ABUSE_GUARD_ENFORCEMENT_ENABLED", "true")
    monkeypatch.setenv("ABUSE_MESSAGES_PER_MINUTE", "100")
    monkeypatch.setenv("ABUSE_MESSAGES_PER_HOUR", "100")
    monkeypatch.setenv("ABUSE_MEDIA_PER_HOUR", "5")
    repository = _repository(tmp_path, monkeypatch)
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)

    for _ in range(5):
        assert repository.check("49123", has_media=True, now=now).allowed is True
    blocked = repository.check("49123", has_media=True, now=now)

    assert blocked.allowed is False
    assert blocked.reason == "media_volume"


def test_user_linked_guard_state_is_deletable_and_events_are_anonymous(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ABUSE_GUARD_ENFORCEMENT_ENABLED", "true")
    repository = _repository(tmp_path, monkeypatch)
    phone = "491234567"
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    for _ in range(6):
        repository.check(phone, now=now)

    assert repository.delete_user(phone) is True
    serialized = str(repository.aggregate(now=now))
    assert phone not in serialized
    assert repository.check(phone, now=now + timedelta(minutes=2)).allowed is True


def test_cleanup_removes_old_windows_and_events(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ABUSE_GUARD_ENFORCEMENT_ENABLED", "false")
    repository = _repository(tmp_path, monkeypatch)
    old = datetime(2026, 6, 1, 12, tzinfo=UTC)
    for _ in range(6):
        repository.check("49123", now=old)

    cleaned = repository.cleanup(now=datetime(2026, 7, 26, 12, tzinfo=UTC), retention_days=30)

    assert cleaned["windows"] > 0
    assert cleaned["events"] > 0
