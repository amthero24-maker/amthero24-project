"""Application composition tests for entitlement behavior."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

import entitlement_extensions
from data_store import JsonDataStore


def _replace_store(tmp_path, monkeypatch) -> JsonDataStore:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "store.json")
    entitlement_extensions.core.store = store
    entitlement_extensions.core._hero_memory_store = entitlement_extensions.core.HeroMemory(store)
    entitlement_extensions._ENTITLEMENT_REPOSITORY = None
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
async def test_plan_request_returns_usage_without_calling_ai(tmp_path, monkeypatch) -> None:
    store = _replace_store(tmp_path, monkeypatch)
    _seed_user(store)
    message = entitlement_extensions.core.IncomingMessage("plan-1", "49123", "شو خطتي", "text")
    store.claim_message("plan-1", "49123", "شو خطتي")

    with patch.object(entitlement_extensions.core, "send_whatsapp_message", new=AsyncMock()) as send, patch.object(
        entitlement_extensions, "_ORIGINAL_PROCESS_INCOMING", new=AsyncMock()
    ) as original:
        await entitlement_extensions.process_incoming(message)

    original.assert_not_awaited()
    assert "خطتك الحالية" in send.await_args.args[1]
    assert store.snapshot()["messages"]["plan-1"]["status"] == "sent"


@pytest.mark.anyio
async def test_observe_only_tracks_media_and_routes_normally(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ENTITLEMENT_DEFAULT_PLAN", "beta")
    monkeypatch.setenv("ENTITLEMENT_BETA_IMAGES_MONTHLY", "0")
    monkeypatch.setenv("ENTITLEMENT_ENFORCEMENT_ENABLED", "false")
    store = _replace_store(tmp_path, monkeypatch)
    _seed_user(store)
    message = entitlement_extensions.core.IncomingMessage("img-1", "49123", "", "image", "media-1", "image/jpeg")

    original = AsyncMock()
    with patch.object(entitlement_extensions, "_ORIGINAL_PROCESS_INCOMING", new=original):
        await entitlement_extensions.process_incoming(message)

    original.assert_awaited_once_with(message)
    assert entitlement_extensions._repository().summary("49123")["usage"]["images_monthly"] == 1


@pytest.mark.anyio
async def test_enforced_media_limit_blocks_but_keeps_text_available(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ENTITLEMENT_DEFAULT_PLAN", "free")
    monkeypatch.setenv("ENTITLEMENT_FREE_IMAGES_MONTHLY", "0")
    monkeypatch.setenv("ENTITLEMENT_ENFORCEMENT_ENABLED", "true")
    store = _replace_store(tmp_path, monkeypatch)
    _seed_user(store)
    message = entitlement_extensions.core.IncomingMessage("img-block", "49123", "", "image", "media-2", "image/jpeg")
    store.claim_message("img-block", "49123", "", message_type="image", media_id="media-2")

    with patch.object(entitlement_extensions.core, "send_whatsapp_message", new=AsyncMock()) as send, patch.object(
        entitlement_extensions, "_ORIGINAL_PROCESS_INCOMING", new=AsyncMock()
    ) as original:
        await entitlement_extensions.process_incoming(message)

    original.assert_not_awaited()
    assert "فيك تكمل معي بالنص" in send.await_args.args[1]
    assert store.snapshot()["messages"]["img-block"]["status"] == "sent"
