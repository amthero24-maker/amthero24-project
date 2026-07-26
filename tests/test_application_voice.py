"""Application-level voice-note tests."""
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

import application
from data_store import JsonDataStore
from voice_service import VoiceServiceError


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


def test_audio_payload_is_accepted() -> None:
    parsed = application.message_from_payload({
        "id": "voice-1",
        "from": "49123",
        "type": "audio",
        "audio": {"id": "media-voice", "mime_type": "audio/ogg; codecs=opus", "voice": True},
    })
    assert parsed is not None
    assert parsed.message_type == "audio"
    assert parsed.media_id == "media-voice"
    assert parsed.mime_type.startswith("audio/ogg")


@pytest.mark.anyio
async def test_voice_note_is_transcribed_and_routed_as_user_text(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    application.core.store = JsonDataStore(tmp_path / "store.json")
    application.core._hero_memory_store = application.core.HeroMemory(application.core.store)
    _seed_user(application.core.store)
    message = application.core.IncomingMessage("voice-ok", "49123", "", "audio", "media-1", "audio/ogg")
    application.core.store.claim_message("voice-ok", "49123", "", message_type="audio", media_id="media-1")

    with patch.object(application.core, "get_media_url", new=AsyncMock(return_value="https://media.test/1")), patch.object(
        application.core, "download_media_bytes", new=AsyncMock(return_value=b"audio")
    ), patch.object(application, "transcribe_audio", return_value="عندي فاتورة وبدي افهمها"), patch.object(
        application.core, "generate_reply", return_value="أكيد، ابعتلي الفاتورة وأنا بشرحلك المهم."
    ), patch.object(application.core, "send_whatsapp_message", new=AsyncMock()) as send:
        await application.process_incoming(message)

    send.assert_awaited_once_with("49123", "أكيد، ابعتلي الفاتورة وأنا بشرحلك المهم.")
    profile = application.core.store.get_user("49123")
    assert profile["last_message"] == "عندي فاتورة وبدي افهمها"
    assert profile["preferred_language"] == "ar"
    assert application.core.store.snapshot()["messages"]["voice-ok"]["status"] == "sent"


@pytest.mark.anyio
async def test_voice_transcription_failure_returns_localized_safe_message(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    application.core.store = JsonDataStore(tmp_path / "store.json")
    application.core._hero_memory_store = application.core.HeroMemory(application.core.store)
    _seed_user(application.core.store)
    message = application.core.IncomingMessage("voice-fail", "49123", "", "audio", "media-2", "audio/ogg")
    application.core.store.claim_message("voice-fail", "49123", "", message_type="audio", media_id="media-2")

    with patch.object(application.core, "get_media_url", new=AsyncMock(return_value="https://media.test/2")), patch.object(
        application.core, "download_media_bytes", new=AsyncMock(return_value=b"audio")
    ), patch.object(application, "transcribe_audio", side_effect=VoiceServiceError("failed")), patch.object(
        application.core, "send_whatsapp_message", new=AsyncMock()
    ) as send:
        await application.process_incoming(message)

    assert "ما قدرت أفهم التسجيل" in send.await_args.args[1]
    assert application.core.store.snapshot()["messages"]["voice-fail"]["status"] == "failed"
