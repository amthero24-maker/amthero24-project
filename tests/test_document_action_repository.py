"""Pending document action repository tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from data_store import JsonDataStore
from document_action_repository import PendingDocumentRepository


def test_pending_action_is_sanitized_short_lived_and_deletable(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    repository = PendingDocumentRepository(store)
    now = datetime(2026, 7, 26, 10, tzinfo=UTC)
    repository.put(
        "491234567",
        {
            "title": " WKK   Rechnung ",
            "topic": "invoice",
            "due_at": "2026-08-10",
            "next_step": "Betrag prüfen",
            "authority": "WKK",
            "source_kind": "pdf",
            "references": ["SECRET-123"],
            "raw_text": "very private document text",
        },
        now=now,
    )

    action = repository.get("491234567", now=now + timedelta(hours=1))
    assert action == {
        "title": "WKK Rechnung",
        "topic": "invoice",
        "due_at": "2026-08-10",
        "next_step": "Betrag prüfen",
        "authority": "WKK",
        "source_kind": "pdf",
    }
    serialized = (tmp_path / "store.json").read_text(encoding="utf-8")
    assert "SECRET-123" not in serialized
    assert "very private document text" not in serialized
    assert "491234567" not in serialized

    assert repository.delete("491234567") is True
    assert repository.get("491234567", now=now) is None


def test_expired_pending_action_is_removed(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    repository = PendingDocumentRepository(store)
    now = datetime(2026, 7, 26, 10, tzinfo=UTC)
    repository.put("49123", {"title": "Task", "topic": "document"}, now=now, ttl=timedelta(minutes=5))

    assert repository.get("49123", now=now + timedelta(minutes=6)) is None
    assert store.snapshot()["pending_document_actions"] == {}
