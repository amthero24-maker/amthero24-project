"""Groq speech-to-text boundary for WhatsApp voice notes."""
from __future__ import annotations

import os
import re
import unicodedata

from groq import Groq

from config import required_env

_DEFAULT_MODEL = "whisper-large-v3-turbo"
_MAX_AUDIO_BYTES = 25 * 1024 * 1024
_LANGUAGE_HINTS = {"de": "de", "ar": "ar", "en": "en", "uk": "uk", "el": "el"}
_EXTENSION_BY_MIME = {
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/webm": "webm",
    "audio/flac": "flac",
}


class VoiceServiceError(RuntimeError):
    """Raised when a voice note cannot be safely transcribed."""


def _safe_mime(mime_type: str) -> str:
    return (mime_type or "audio/ogg").split(";", 1)[0].strip().casefold()


def _filename(mime_type: str) -> str:
    extension = _EXTENSION_BY_MIME.get(_safe_mime(mime_type), "ogg")
    return f"whatsapp-voice.{extension}"


def _clean_transcript(value: str) -> str:
    transcript = unicodedata.normalize("NFKC", value or "")
    transcript = "".join(
        character
        for character in transcript
        if unicodedata.category(character) not in {"Cf", "Cs"} or character in {"\n", "\t"}
    )
    transcript = re.sub(r"[ \t]{2,}", " ", transcript)
    transcript = re.sub(r" *\n *", "\n", transcript).strip()
    if not transcript:
        raise VoiceServiceError("Voice transcription was empty")
    return transcript[:5000]


def transcribe_audio(audio_bytes: bytes, *, mime_type: str, language_hint: str = "") -> str:
    """Transcribe one WhatsApp voice note into its original language."""
    if not audio_bytes:
        raise VoiceServiceError("Voice note is empty")
    if len(audio_bytes) > _MAX_AUDIO_BYTES:
        raise VoiceServiceError("Voice note exceeds the supported size")

    try:
        client = Groq(api_key=required_env("GROQ_API_KEY"))
        request: dict[str, object] = {
            "file": (_filename(mime_type), audio_bytes),
            "model": os.getenv("GROQ_TRANSCRIPTION_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL,
            "response_format": "json",
            "temperature": 0.0,
        }
        language = _LANGUAGE_HINTS.get(language_hint)
        if language:
            request["language"] = language
        result = client.audio.transcriptions.create(**request)
        return _clean_transcript(str(getattr(result, "text", "") or ""))
    except VoiceServiceError:
        raise
    except Exception as exc:
        raise VoiceServiceError("Voice transcription failed") from exc
