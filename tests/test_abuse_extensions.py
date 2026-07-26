"""Top-level application tests for abuse protection."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

import abuse_extensions
from data_store import JsonDataStore


def _replace_store(tmp_path, monkeypatch) -> JsonDataStore:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ABUSE_GUARD_ENABLED", "true")
    monkeypatch.setenv("ABUSE_GUARD_ENFORCEMENT_ENABLED", "true")
    monkeypatch.setenv("ABUSE_MESSAGES_PER_MINUTE", "5")
    monkeypatch.setenv("ABUSE_MESSAGES_PER_HOUR", "100")
    monkeypatch.setenv("ABUSE_MEDIA_PER_HOUR", "100")
    store = JsonDataStore(tmp_path / "store.json")
    abuse_extensions.core.store = store
    abuse_extensions.core._hero_memory_store = abuse_extensions.core.HeroMemory(store)
    abuse_extensions._ABUSE_REPOSITORY = None
    return store


def _seed_user(store: JsonDataStore) -> None:
    store.update_user("49123", {
        "memory_consent": "granted",
        "memory_consent_at": datetime.now(UTC).isoformat(),
        "memory_consent_version": "test-v1",
        "onboarding_stage": "complete",
        "intro_sent_at": datetime.now(UTC).isoformat(),
        "preferred_language": "ar",
        "first_name": "وسام",
    })


@pytest.mark.anyio
async def test_normal_messages_route_and_burst_is_stopped_once(tmp_path, monkeypatch) -> None:
    store = _replace_store(tmp_path, monkeypatch)
    _seed_user(store)
    original = AsyncMock()

    with patch.object(abuse_extensions, "_ORIGINAL_PROCESS_INCOMING", new=original), patch.object(
        abuse_extensions.core, "send_whatsapp_message", new=AsyncMock()
    ) as send:
        for index in range(5):
            message = abuse_extensions.core.IncomingMessage(f"ok-{index}", "49123", "مرحبا", "text")
            await abuse_extensions.process_incoming(message)
        blocked = abuse_extensions.core.IncomingMessage("blocked-1", "49123", "مرحبا", "text")
        store.claim_message("blocked-1", "49123", "مرحبا")
        await abuse_extensions.process_incoming(blocked)
        repeated = abuse_extensions.core.IncomingMessage("blocked-2", "49123", "مرحبا", "text")
        store.claim_message("blocked-2", "49123", "مرحبا")
        await abuse_extensions.process_incoming(repeated)

    assert original.await_count == 5
    assert send.await_count == 1
    assert "وصلتني رسائل كثيرة" in send.await_args.args[1]
    assert store.snapshot()["messages"]["blocked-2"]["status"] == "rate_limited"


@pytest.mark.anyio
async def test_privacy_deletion_command_bypasses_active_block(tmp_path, monkeypatch) -> None:
    store = _replace_store(tmp_path, monkeypatch)
    _seed_user(store)
    repository = abuse_extensions._repository()
    now = datetime.now(UTC)
    for _ in range(6):
        repository.check("49123", now=now)

    message = abuse_extensions.core.IncomingMessage("delete-1", "49123", "امسح بياناتي", "text")
    original = AsyncMock()
    with patch.object(abuse_extensions, "_ORIGINAL_PROCESS_INCOMING", new=original):
        await abuse_extensions.process_incoming(message)

    original.assert_awaited_once_with(message)
