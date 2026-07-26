"""Configuration and safety tests for the optional durable inbound queue."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from durable_queue import (
    DurableQueueRepository,
    QueueServiceError,
    queue_enabled,
    queue_encryption_status,
    queue_poll_seconds,
    queue_status,
)


def test_queue_is_disabled_by_default_and_does_not_require_a_key(monkeypatch) -> None:
    monkeypatch.delenv("DURABLE_QUEUE_ENABLED", raising=False)
    monkeypatch.delenv("MESSAGE_QUEUE_ENCRYPTION_KEY", raising=False)

    assert queue_enabled() is False
    assert queue_status(SimpleNamespace(backend_name="json")) == "disabled"
    assert queue_encryption_status() == "missing"


def test_explicit_enablement_requires_postgres_and_a_strong_dedicated_key(monkeypatch) -> None:
    monkeypatch.setenv("DURABLE_QUEUE_ENABLED", "true")
    monkeypatch.setenv("MESSAGE_QUEUE_ENCRYPTION_KEY", "weak")

    assert queue_status(SimpleNamespace(backend_name="json")) == "requires-postgresql"
    assert queue_status(SimpleNamespace(backend_name="postgresql")) == "misconfigured"

    monkeypatch.setenv("MESSAGE_QUEUE_ENCRYPTION_KEY", "durable-queue-production-key-2026-unique-A7mQ2xP9")
    assert queue_status(SimpleNamespace(backend_name="postgresql")) == "configured"


def test_poll_interval_is_bounded(monkeypatch) -> None:
    monkeypatch.setenv("DURABLE_QUEUE_POLL_SECONDS", "0")
    assert queue_poll_seconds() == 1
    monkeypatch.setenv("DURABLE_QUEUE_POLL_SECONDS", "99999")
    assert queue_poll_seconds() == 300
    monkeypatch.setenv("DURABLE_QUEUE_POLL_SECONDS", "invalid")
    assert queue_poll_seconds() == 5


def test_repository_refuses_non_postgres_queue_storage(monkeypatch) -> None:
    monkeypatch.setenv("MESSAGE_QUEUE_ENCRYPTION_KEY", "durable-queue-production-key-2026-unique-A7mQ2xP9")
    repository = DurableQueueRepository(SimpleNamespace(backend_name="json"))

    with pytest.raises(QueueServiceError, match="queue_requires_postgresql"):
        repository.enqueue("wamid.local", "+49170")
