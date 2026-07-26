"""Provider telemetry, circuit, aggregate, and retention tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from data_store import JsonDataStore
from provider_reliability import ProviderReliabilityRepository


def _repository(tmp_path, monkeypatch) -> ProviderReliabilityRepository:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("PROVIDER_TELEMETRY_ENABLED", "true")
    monkeypatch.setenv("GROQ_CIRCUIT_BREAKER_ENABLED", "true")
    monkeypatch.setenv("GROQ_CIRCUIT_FAILURE_THRESHOLD", "3")
    monkeypatch.setenv("GROQ_CIRCUIT_COOLDOWN_SECONDS", "60")
    return ProviderReliabilityRepository(JsonDataStore(tmp_path / "store.json"))


def test_groq_circuit_opens_after_failures_and_recovers(tmp_path, monkeypatch) -> None:
    repository = _repository(tmp_path, monkeypatch)
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)

    for second in range(3):
        repository.record("groq", "generate_reply", "failure", 100, error_code="GroqServiceError", now=now + timedelta(seconds=second))

    blocked = repository.before_call("groq", now=now + timedelta(seconds=3))
    assert blocked.allowed is False
    assert repository.circuit_status("groq", now=now + timedelta(seconds=3)) == "open"

    after_cooldown = now + timedelta(seconds=70)
    assert repository.before_call("groq", now=after_cooldown).allowed is True
    repository.record("groq", "generate_reply", "success", 50, now=after_cooldown)
    assert repository.circuit_status("groq", now=after_cooldown) == "closed"


def test_whatsapp_failures_do_not_open_groq_circuit(tmp_path, monkeypatch) -> None:
    repository = _repository(tmp_path, monkeypatch)
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    for _ in range(10):
        repository.record("whatsapp", "send_text", "failure", 200, error_code="WhatsAppServiceError", now=now)
    assert repository.before_call("groq", now=now).allowed is True


def test_aggregate_contains_only_anonymous_operational_data(tmp_path, monkeypatch) -> None:
    repository = _repository(tmp_path, monkeypatch)
    now = datetime(2026, 7, 26, 12, tzinfo=UTC)
    repository.record("groq", "generate_reply", "success", 100, now=now)
    repository.record("groq", "generate_reply", "success", 300, now=now)
    repository.record("whatsapp", "send_text", "failure", 200, error_code="TimeoutError", now=now)

    overview = repository.aggregate(now=now)
    serialized = str(overview)

    assert overview["groq"]["success"] == 2
    assert overview["groq"]["latency_ms"]["average"] == 200
    assert overview["whatsapp"]["failure"] == 1
    assert "TimeoutError" in overview["whatsapp"]["errors"]
    for forbidden in ("49123", "وسام", "prompt", "recipient"):
        assert forbidden not in serialized


def test_cleanup_removes_old_anonymous_events(tmp_path, monkeypatch) -> None:
    repository = _repository(tmp_path, monkeypatch)
    repository.record("groq", "generate_reply", "success", 100, now=datetime(2026, 6, 1, tzinfo=UTC))
    removed = repository.cleanup(now=datetime(2026, 7, 26, tzinfo=UTC), retention_days=30)
    assert removed == 1
    assert repository.aggregate(now=datetime(2026, 7, 26, tzinfo=UTC))["groq"]["total"] == 0
