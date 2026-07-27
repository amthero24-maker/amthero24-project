"""Production composition root for AmtHero24.

The stable webhook remains in ``app.py``. This module layers relationship,
mission, voice, and PDF intelligence on top without duplicating the webhook.
"""
from __future__ import annotations

from typing import Any

import app as core
from document_service import DocumentServiceError, build_pdf_request, extract_pdf_text
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


def _preferred_language(profile: dict[str, Any]) -> str:
    memory_enabled = profile.get("memory_consent") == "granted"
    language = str(
        profile.get("preferred_language") if memory_enabled else profile.get("session_language")
        or profile.get("preferred_language")
        or "de"
    )
    return language if language in {"de", "ar", "en", "uk", "el"} else "de"


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


def _pdf_failure(language: str, code: str) -> str:
    messages = {
        "scanned": {
            "ar": "الـPDF يبدو مصوّرًا وما فيه نص قابل للقراءة. ابعت الصفحات المهمة كصور واضحة وأنا بشرحها فورًا.",
            "de": "Die PDF scheint nur aus Scans zu bestehen. Sende die wichtigen Seiten bitte als klare Bilder.",
            "en": "The PDF appears to contain scanned images only. Please send the important pages as clear images.",
            "uk": "Схоже, PDF містить лише скановані зображення. Надішли важливі сторінки як чіткі фото.",
            "el": "Το PDF φαίνεται να περιέχει μόνο σαρωμένες εικόνες. Στείλε τις σημαντικές σελίδες ως καθαρές εικόνες.",
        },
        "encrypted": {
            "ar": "الـPDF محمي بكلمة مرور. افتحه عندك واحفظ نسخة غير محمية، أو ابعت الصفحات المهمة كصور.",
            "de": "Die PDF ist passwortgeschützt. Speichere bitte eine ungeschützte Kopie oder sende die wichtigen Seiten als Bilder.",
            "en": "The PDF is password-protected. Save an unlocked copy or send the important pages as images.",
            "uk": "PDF захищено паролем. Збережи незахищену копію або надішли важливі сторінки як зображення.",
            "el": "Το PDF προστατεύεται με κωδικό. Αποθήκευσε ένα ξεκλείδωτο αντίγραφο ή στείλε τις σημαντικές σελίδες ως εικόνες.",
        },
        "too_large": {
            "ar": "حجم الـPDF كبير. ابعت الجزء أو الصفحات المهمة فقط.",
            "de": "Die PDF ist zu groß. Sende bitte nur den wichtigen Teil oder die relevanten Seiten.",
            "en": "The PDF is too large. Please send only the important section or relevant pages.",
            "uk": "PDF завеликий. Надішли лише важливу частину або потрібні сторінки.",
            "el": "Το PDF είναι πολύ μεγάλο. Στείλε μόνο το σημαντικό τμήμα ή τις σχετικές σελίδες.",
        },
    }
    generic = {
        "ar": "ما قدرت أفتح ملف الـPDF بشكل آمن. جرّب إرساله مرة ثانية أو ابعت الصفحات المهمة كصور.",
        "de": "Ich konnte die PDF nicht sicher öffnen. Sende sie erneut oder schick die wichtigen Seiten als Bilder.",
        "en": "I could not safely open the PDF. Please resend it or send the important pages as images.",
        "uk": "Не вдалося безпечно відкрити PDF. Надішли його ще раз або надішли важливі сторінки як зображення.",
        "el": "Δεν μπόρεσα να ανοίξω με ασφάλεια το PDF. Στείλε το ξανά ή στείλε τις σημαντικές σελίδες ως εικόνες.",
    }
    return messages.get(code, generic).get(language, generic["de"])


async def _transcribe_message(message: core.IncomingMessage) -> core.IncomingMessage:
    language = _preferred_language(core.store.get_user(message.sender))
    media_url = await core.get_media_url(str(message.media_id))
    audio_bytes = await core.download_media_bytes(media_url)
    transcript = await core.anyio.to_thread.run_sync(
        lambda: transcribe_audio(audio_bytes, mime_type=message.mime_type, language_hint=language)
    )
    return core.IncomingMessage(message.message_id, message.sender, transcript, "text")


async def _extract_pdf_message(message: core.IncomingMessage, language: str) -> core.IncomingMessage:
    media_url = await core.get_media_url(str(message.media_id))
    pdf_bytes = await core.download_media_bytes(media_url)
    extraction = await core.anyio.to_thread.run_sync(lambda: extract_pdf_text(pdf_bytes))
    request = build_pdf_request(extraction, language=language, note=message.text)
    return core.IncomingMessage(
        message.message_id,
        message.sender,
        request,
        "text",
        internal_context="document_analysis",
    )


async def process_incoming(message: core.IncomingMessage) -> None:
    """Normalize voice/PDF input, persist preferences, then route normally."""
    pdf_processed = False
    profile = core.store.get_user(message.sender)
    language = _preferred_language(profile)

    if message.message_type == "audio":
        try:
            message = await _transcribe_message(message)
        except (VoiceServiceError, RuntimeError, ValueError):
            core.store.update_message_status(message.message_id, "failed")
            await core.send_whatsapp_message(message.sender, _voice_failure(language))
            return

    mime_type = (message.mime_type or "").split(";", 1)[0].strip().casefold()
    if message.message_type == "document" and mime_type == "application/pdf":
        try:
            message = await _extract_pdf_message(message, language)
            pdf_processed = True
            context_updates: dict[str, Any] = {
                "session_language": language,
                "session_topic": "document",
                "session_expires_at": core._session_expiry(),
            }
            if profile.get("memory_consent") == "granted":
                context_updates["current_topic"] = "document"
            core.store.update_user(message.sender, context_updates)
        except DocumentServiceError as exc:
            core.store.update_message_status(message.message_id, "failed")
            await core.send_whatsapp_message(message.sender, _pdf_failure(language, exc.code))
            return
        except (RuntimeError, ValueError):
            core.store.update_message_status(message.message_id, "failed")
            await core.send_whatsapp_message(message.sender, _pdf_failure(language, "invalid"))
            return

    profile = core.store.get_user(message.sender)
    memory_enabled = profile.get("memory_consent") == "granted"
    previous_language = _preferred_language(profile)
    language = core.detect_language(message.text, previous_language) if message.text.strip() else previous_language
    preference = (
        analyze_preferences(message.text, profile.get("communication_style"))
        if message.internal_context != "document_analysis"
        else None
    )

    if preference is not None and preference.changed:
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

    if pdf_processed and memory_enabled:
        core.store.update_user(message.sender, {
            "last_message": "PDF document processed transiently",
            "conversation_summary": f"Language={language}; topic=document; PDF content processed transiently and not retained",
            "current_topic": "document",
        })


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
