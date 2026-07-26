"""Production composition root for AmtHero24.

The stable webhook remains in ``app.py``. This module layers relationship,
mission, and voice intelligence on top without duplicating the webhook.
"""
from __future__ import annotations

from typing import Any

import app as core
from mission_intelligence import enhanced_detect_mission_intent, enhanced_mission_title
from relationship_engine import (
    analyze_preferences,
    augment_prompt,
    human_export_reply,
    human_memory_summary,
    preference_ack,
    serialize_style,
)
from voice_service import VoiceServiceError, transcribe_audio

_ORIGINAL_PROCESS_INCOMING = core.process_incoming
_ORIGINAL_BUILD_SYSTEM_PROMPT = core.build_system_prompt
_ORIGINAL_DETECT_MISSION_INTENT = core.detect_mission_intent
_ORIGINAL_MISSION_TITLE = core.mission_title
_ORIGINAL_MESSAGE_FROM_PAYLOAD = core._message_from_payload


def build_system_prompt(**kwargs: Any) -> str:
    base = _ORIGINAL_BUILD_SYSTEM_PROMPT(**kwargs)
    return augment_prompt(
        base,
        profile=dict(kwargs.get("profile") or {}),
        text=str(kwargs.get("text") or ""),
        history=list(kwargs.get("history") or []),
    )


def detect_mission_intent(text: str):
    return enhanced_detect_mission_intent(text, _ORIGINAL_DETECT_MISSION_INTENT)


def mission_title(intent, *, current_topic: str = "", last_message: str = "") -> str:
    return enhanced_mission_title(
        intent,
        current_topic=current_topic,
        last_message=last_message,
        original_title=_ORIGINAL_MISSION_TITLE,
    )


def message_from_payload(message: dict[str, Any]) -> core.IncomingMessage | None:
    if str(message.get("type") or "") != "audio":
        return _ORIGINAL_MESSAGE_FROM_PAYLOAD(message)
    message_id = str(message.get("id") or "").strip()
    sender = str(message.get("from") or "").strip()
    audio = message.get("audio") if isinstance(message.get("audio"), dict) else {}
    media_id = str(audio.get("id") or "").strip()
    if not message_id or not sender or not media_id:
        return None
    mime_type = str(audio.get("mime_type") or "audio/ogg")
    return core.IncomingMessage(message_id, sender, "", "audio", media_id, mime_type)


def _voice_failure(language: str) -> str:
    return {
        "ar": "ما قدرت أفهم التسجيل بوضوح 🎙️ جرّب تبعته مرة ثانية بمكان أهدى، أو اكتبلي المطلوب بجملة قصيرة.",
        "de": "Ich konnte die Sprachnachricht nicht klar verstehen 🎙️ Sende sie bitte noch einmal in ruhigerer Umgebung oder schreib den Wunsch kurz.",
        "en": "I could not understand the voice note clearly 🎙️ Please resend it somewhere quieter or type the request briefly.",
        "uk": "Не вдалося чітко розпізнати голосове повідомлення 🎙️ Надішли його ще раз у тихішому місці або коротко напиши запит.",
        "el": "Δεν μπόρεσα να καταλάβω καθαρά το φωνητικό μήνυμα 🎙️ Στείλε το ξανά σε πιο ήσυχο μέρος ή γράψε σύντομα το αίτημα.",
    }.get(language, "I could not understand the voice note clearly. Please resend it.")


async def _transcribe_message(message: core.IncomingMessage) -> core.IncomingMessage:
    profile = core.store.get_user(message.sender)
    memory_enabled = profile.get("memory_consent") == "granted"
    language = str(
        profile.get("preferred_language") if memory_enabled else profile.get("session_language")
        or profile.get("preferred_language")
        or "de"
    )
    media_url = await core.get_media_url(str(message.media_id))
    audio_bytes = await core.download_media_bytes(media_url)
    transcript = await core.anyio.to_thread.run_sync(
        lambda: transcribe_audio(audio_bytes, mime_type=message.mime_type, language_hint=language)
    )
    return core.IncomingMessage(message.message_id, message.sender, transcript, "text")


async def process_incoming(message: core.IncomingMessage) -> None:
    """Transcribe voice, persist explicit preferences, then route normally."""
    if message.message_type == "audio":
        profile = core.store.get_user(message.sender)
        memory_enabled = profile.get("memory_consent") == "granted"
        language = str(
            profile.get("preferred_language") if memory_enabled else profile.get("session_language")
            or profile.get("preferred_language")
            or "de"
        )
        try:
            message = await _transcribe_message(message)
        except (VoiceServiceError, RuntimeError, ValueError):
            core.store.update_message_status(message.message_id, "failed")
            await core.send_whatsapp_message(message.sender, _voice_failure(language))
            return

    profile = core.store.get_user(message.sender)
    memory_enabled = profile.get("memory_consent") == "granted"
    previous_language = str(
        profile.get("preferred_language") if memory_enabled else profile.get("session_language")
        or profile.get("preferred_language")
        or "de"
    )
    language = core.detect_language(message.text, previous_language) if message.text.strip() else previous_language
    preference = analyze_preferences(message.text, profile.get("communication_style"))

    if preference.changed:
        updates: dict[str, Any] = {
            "session_language": language,
            "session_expires_at": core._session_expiry(),
            "last_seen": core._now().isoformat(),
        }
        persisted = memory_enabled and preference.persistent
        if persisted:
            updates["communication_style"] = serialize_style(preference.settings)
        profile = core.store.update_user(message.sender, updates)

        stage = str(profile.get("onboarding_stage") or "")
        settled_user = stage == "complete" or profile.get("memory_consent") in {"granted", "declined"}
        if preference.command_only and settled_user:
            await core._finish(
                message.message_id,
                preference_ack(language, preference, persisted=persisted),
                message.sender,
            )
            return

    await _ORIGINAL_PROCESS_INCOMING(message)


# Patch globals resolved by app.receive_webhook and app.extract_incoming_messages.
core._message_from_payload = message_from_payload
core.build_system_prompt = build_system_prompt
core.memory_summary_message = human_memory_summary
core._export_reply = human_export_reply
core.detect_mission_intent = detect_mission_intent
core.mission_title = mission_title
core.process_incoming = process_incoming

app = core.app
store = core.store
