"""Voice transcription service tests."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import voice_service


def test_whatsapp_opus_mime_uses_ogg_filename() -> None:
    assert voice_service._filename("audio/ogg; codecs=opus") == "whatsapp-voice.ogg"
    assert voice_service._filename("audio/mpeg") == "whatsapp-voice.mp3"


def test_empty_and_oversized_voice_notes_are_rejected() -> None:
    with pytest.raises(voice_service.VoiceServiceError):
        voice_service.transcribe_audio(b"", mime_type="audio/ogg")
    with pytest.raises(voice_service.VoiceServiceError):
        voice_service.transcribe_audio(b"x" * (25 * 1024 * 1024 + 1), mime_type="audio/ogg")


def test_transcription_uses_multilingual_model_and_language_hint(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    create = MagicMock(return_value=SimpleNamespace(text="  مرحبا   بكم  "))
    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))

    with patch.object(voice_service, "Groq", return_value=client):
        transcript = voice_service.transcribe_audio(
            b"audio-bytes",
            mime_type="audio/ogg; codecs=opus",
            language_hint="ar",
        )

    assert transcript == "مرحبا بكم"
    request = create.call_args.kwargs
    assert request["model"] == "whisper-large-v3-turbo"
    assert request["language"] == "ar"
    assert request["file"][0] == "whatsapp-voice.ogg"
    assert request["temperature"] == 0.0


def test_empty_provider_transcript_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    create = MagicMock(return_value=SimpleNamespace(text="   "))
    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
    with patch.object(voice_service, "Groq", return_value=client):
        with pytest.raises(voice_service.VoiceServiceError):
            voice_service.transcribe_audio(b"audio", mime_type="audio/ogg", language_hint="de")
