"""Integration tests for anonymous provider instrumentation."""
from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

import provider_extensions
from data_store import JsonDataStore
from provider_reliability import ProviderCircuitOpen


def _replace_store(tmp_path, monkeypatch) -> JsonDataStore:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("PROVIDER_TELEMETRY_ENABLED", "true")
    monkeypatch.setenv("GROQ_CIRCUIT_BREAKER_ENABLED", "true")
    monkeypatch.setenv("GROQ_CIRCUIT_FAILURE_THRESHOLD", "2")
    monkeypatch.setenv("GROQ_CIRCUIT_COOLDOWN_SECONDS", "60")
    store = JsonDataStore(tmp_path / "store.json")
    provider_extensions.core.store = store
    provider_extensions._PROVIDER_REPOSITORY = None
    return store


def test_generate_reply_records_success_without_prompt_content(tmp_path, monkeypatch) -> None:
    _replace_store(tmp_path, monkeypatch)
    original = Mock(return_value="final answer")
    with patch.object(provider_extensions, "_ORIGINAL_GENERATE_REPLY", new=original):
        result = provider_extensions.generate_reply(system_prompt="secret prompt", user_text="private message")

    assert result == "final answer"
    overview = provider_extensions._repository().aggregate()
    assert overview["groq"]["success"] == 1
    serialized = str(provider_extensions.core.store.snapshot().get("provider_events", []))
    assert "secret prompt" not in serialized
    assert "private message" not in serialized


def test_generate_reply_opens_circuit_and_skips_provider_call(tmp_path, monkeypatch) -> None:
    _replace_store(tmp_path, monkeypatch)
    original = Mock(side_effect=RuntimeError("provider failed"))
    with patch.object(provider_extensions, "_ORIGINAL_GENERATE_REPLY", new=original):
        for _ in range(2):
            with pytest.raises(RuntimeError):
                provider_extensions.generate_reply(system_prompt="x", user_text="y")
        with pytest.raises(ProviderCircuitOpen):
            provider_extensions.generate_reply(system_prompt="x", user_text="y")

    assert original.call_count == 2
    assert provider_extensions._repository().aggregate()["groq"]["circuit_rejected"] == 1


@pytest.mark.anyio
async def test_whatsapp_send_records_outcome_without_recipient(tmp_path, monkeypatch) -> None:
    _replace_store(tmp_path, monkeypatch)
    original = AsyncMock(return_value=[{"ok": True}])
    with patch.object(provider_extensions, "_ORIGINAL_SEND_TEXT", new=original):
        result = await provider_extensions.send_whatsapp_message("491234567", "private text")

    assert result == [{"ok": True}]
    overview = provider_extensions._repository().aggregate()
    assert overview["whatsapp"]["success"] == 1
    serialized = str(provider_extensions.core.store.snapshot().get("provider_events", []))
    assert "491234567" not in serialized
    assert "private text" not in serialized
