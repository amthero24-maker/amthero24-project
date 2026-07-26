"""Office-document composition layer for the AmtHero24 production app."""
from __future__ import annotations

from typing import Any

import application as composed
from document_service import (
    DocumentServiceError,
    build_document_request,
    extract_docx_text,
    extract_plain_text,
)

core = composed.core
_ORIGINAL_PROCESS_INCOMING = composed.process_incoming

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_TEXT_MIMES = {"text/plain", "text/csv", "application/csv"}


def _kind(message: core.IncomingMessage) -> str:
    mime = (message.mime_type or "").split(";", 1)[0].strip().casefold()
    filename = (message.text or "").strip().casefold()
    if mime == _DOCX_MIME or filename.endswith(".docx"):
        return "docx"
    if mime in _TEXT_MIMES or filename.endswith((".txt", ".csv")):
        return "text"
    if filename.endswith(".doc") or mime == "application/msword":
        return "legacy_word"
    return ""


def _failure(language: str, code: str) -> str:
    if code == "legacy_word":
        return {
            "ar": "ملف Word القديم بصيغة .doc ما بينقرأ بأمان هون. احفظه كـ .docx أو PDF وابعت النسخة الجديدة.",
            "de": "Das alte Word-Format .doc kann ich hier nicht sicher lesen. Speichere es als .docx oder PDF und sende die neue Datei.",
            "en": "I cannot safely read the old .doc format here. Save it as .docx or PDF and send the new file.",
            "uk": "Старий формат Word .doc неможливо безпечно прочитати. Збережи файл як .docx або PDF і надішли нову версію.",
            "el": "Δεν μπορώ να διαβάσω με ασφάλεια την παλιά μορφή .doc. Αποθήκευσέ το ως .docx ή PDF και στείλε το νέο αρχείο.",
        }.get(language, "Please convert the old .doc file to .docx or PDF.")
    if code == "unsafe_archive":
        return {
            "ar": "ملف Word بنيته غير آمنة أو حجمه الداخلي كبير جدًا. افتحه عندك واحفظ نسخة جديدة كـ PDF أو DOCX.",
            "de": "Die interne Struktur der Word-Datei ist unsicher oder ungewöhnlich groß. Speichere eine neue Kopie als PDF oder DOCX.",
            "en": "The Word file has an unsafe or unusually large internal structure. Save a fresh copy as PDF or DOCX.",
            "uk": "Файл Word має небезпечну або надто велику внутрішню структуру. Збережи нову копію як PDF або DOCX.",
            "el": "Το αρχείο Word έχει μη ασφαλή ή υπερβολικά μεγάλη εσωτερική δομή. Αποθήκευσε νέο αντίγραφο ως PDF ή DOCX.",
        }.get(language, "The Word file could not be processed safely.")
    generic = {
        "ar": "ما قدرت اقرأ الملف بوضوح. جرّب تحفظه من جديد كـ DOCX أو PDF، أو انسخ النص المهم هون.",
        "de": "Ich konnte die Datei nicht klar lesen. Speichere sie erneut als DOCX oder PDF oder kopiere den wichtigen Text hierher.",
        "en": "I could not read the file clearly. Save it again as DOCX or PDF, or paste the important text here.",
        "uk": "Не вдалося чітко прочитати файл. Збережи його знову як DOCX або PDF чи встав важливий текст сюди.",
        "el": "Δεν μπόρεσα να διαβάσω καθαρά το αρχείο. Αποθήκευσέ το ξανά ως DOCX ή PDF ή επικόλλησε εδώ το σημαντικό κείμενο.",
    }
    return generic.get(language, generic["de"])


async def _normalize_document(message: core.IncomingMessage, kind: str, language: str) -> core.IncomingMessage:
    media_url = await core.get_media_url(str(message.media_id))
    content = await core.download_media_bytes(media_url)
    if kind == "docx":
        extraction = await core.anyio.to_thread.run_sync(lambda: extract_docx_text(content))
    else:
        extraction = await core.anyio.to_thread.run_sync(lambda: extract_plain_text(content))
    request = build_document_request(extraction, language=language, note=message.text)
    return core.IncomingMessage(message.message_id, message.sender, request, "text")


async def process_incoming(message: core.IncomingMessage) -> None:
    kind = _kind(message) if message.message_type == "document" else ""
    if not kind:
        await _ORIGINAL_PROCESS_INCOMING(message)
        return

    profile = core.store.get_user(message.sender)
    language = composed._preferred_language(profile)
    if kind == "legacy_word":
        core.store.update_message_status(message.message_id, "failed")
        await core.send_whatsapp_message(message.sender, _failure(language, kind))
        return

    try:
        normalized = await _normalize_document(message, kind, language)
    except DocumentServiceError as exc:
        core.store.update_message_status(message.message_id, "failed")
        await core.send_whatsapp_message(message.sender, _failure(language, exc.code))
        return
    except (RuntimeError, ValueError):
        core.store.update_message_status(message.message_id, "failed")
        await core.send_whatsapp_message(message.sender, _failure(language, "invalid"))
        return

    context_updates: dict[str, Any] = {
        "session_language": language,
        "session_topic": "document",
        "session_expires_at": core._session_expiry(),
    }
    memory_enabled = profile.get("memory_consent") == "granted"
    if memory_enabled:
        context_updates["current_topic"] = "document"
    core.store.update_user(message.sender, context_updates)

    await _ORIGINAL_PROCESS_INCOMING(normalized)

    if memory_enabled:
        core.store.update_user(message.sender, {
            "last_message": f"{kind.upper()} document processed transiently",
            "conversation_summary": (
                f"Language={language}; topic=document; {kind.upper()} content processed transiently and not retained"
            ),
            "current_topic": "document",
        })


# Replace the request-time webhook target after the existing composition is loaded.
composed.process_incoming = process_incoming
core.process_incoming = process_incoming

app = composed.app
store = composed.store
