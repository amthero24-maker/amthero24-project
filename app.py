"""AmtHero24 FastAPI entrypoint and WhatsApp webhook."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import anyio
from fastapi import BackgroundTasks, FastAPI, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from config import APP_VERSION, DATA_STORE_PATH, GROQ_MODEL, required_env
from data_store import JsonDataStore
from groq_client import generate_reply
from prompts import build_system_prompt
from whatsapp import download_media_bytes, get_media_url, send_whatsapp_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("amthero24")

app = FastAPI(title="AmtHero24", version=APP_VERSION)
store = JsonDataStore(DATA_STORE_PATH)


@dataclass(frozen=True)
class IncomingMessage:
    message_id: str
    sender: str
    text: str
    message_type: str
    media_id: str | None = None
    mime_type: str = "application/octet-stream"


def detect_language(text: str) -> str:
    if re.search(r"[\u0600-\u06FF]", text or ""):
        return "ar"
    if re.search(r"[\u0400-\u04FF]", text or ""):
        return "uk"
    if re.search(r"[\u0370-\u03FF]", text or ""):
        return "el"
    lowered = (text or "").lower()
    if any(word in lowered for word in ("hello", "please", "thanks", "help")):
        return "en"
    return "de"


def extract_name(text: str) -> str:
    cleaned = (text or "").strip()
    patterns = (
        r"(?:اسمي|أنا|انا)\s+([\u0600-\u06FFA-Za-zÄÖÜäöüß-]{2,30})",
        r"(?:ich heiße|ich bin|mein name ist)\s+([A-Za-zÄÖÜäöüß-]{2,30})",
        r"(?:my name is|i am)\s+([A-Za-z-]{2,30})",
        r"(?:мене звати|я)\s+([\u0400-\u04FF-]{2,30})",
        r"(?:με λένε|είμαι)\s+([\u0370-\u03FF-]{2,30})",
    )
    for pattern in patterns:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if match:
            return match.group(1)

    # Preserve the proven WhatsApp flow where a user replies with only their name.
    if 1 <= len(cleaned.split()) <= 2 and len(cleaned) <= 40 and not re.search(r"[?!؟]", cleaned):
        if re.fullmatch(r"[\u0600-\u06FFA-Za-zÄÖÜäöüß\u0400-\u04FF\u0370-\u03FF' -]+", cleaned):
            ignored = {
                "مرحبا", "أهلا", "اهلا", "سلام", "هلا", "تمام", "نعم", "لا", "شكرا", "مساعدة",
                "hallo", "hi", "hilfe", "danke", "ja", "nein", "okay", "ok",
                "hello", "help", "thanks", "yes", "no",
                "привіт", "так", "ні", "дякую", "βοήθεια", "γεια", "ναι", "όχι",
            }
            if cleaned.casefold() not in {item.casefold() for item in ignored}:
                return cleaned.split()[0]
    return ""


def _message_from_payload(message: dict[str, Any]) -> IncomingMessage | None:
    message_id = str(message.get("id", "")).strip()
    sender = str(message.get("from", "")).strip()
    message_type = str(message.get("type", "")).strip()
    if not message_id or not sender:
        return None

    text = ""
    media_id: str | None = None
    mime_type = "application/octet-stream"

    if message_type == "text":
        text = str(message.get("text", {}).get("body", ""))
    elif message_type == "button":
        text = str(message.get("button", {}).get("text", ""))
    elif message_type == "interactive":
        interactive = message.get("interactive", {})
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        text = str(reply.get("title") or reply.get("id") or "")
    elif message_type in {"image", "document"}:
        media = message.get(message_type, {})
        media_id = str(media.get("id", "")) or None
        text = str(media.get("caption") or media.get("filename") or "")
        mime_type = str(media.get("mime_type") or mime_type)
        if not media_id:
            return None
    else:
        return None

    return IncomingMessage(message_id, sender, text, message_type, media_id, mime_type)


def extract_incoming_messages(payload: Any) -> list[IncomingMessage]:
    if not isinstance(payload, dict):
        return []
    extracted: list[IncomingMessage] = []
    entries = payload.get("entry", [])
    if not isinstance(entries, list):
        return []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        changes = entry.get("changes", [])
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict):
                continue
            value = change.get("value", {})
            if not isinstance(value, dict):
                continue
            messages = value.get("messages", [])
            if not isinstance(messages, list):
                continue
            for message in messages:
                if isinstance(message, dict):
                    parsed = _message_from_payload(message)
                    if parsed:
                        extracted.append(parsed)
    return extracted


def extract_text_messages(payload: Any) -> list[tuple[str, str, str]]:
    return [
        (item.message_id, item.sender, item.text)
        for item in extract_incoming_messages(payload)
        if item.message_type in {"text", "button", "interactive"}
    ]


def _safe_failure(language: str, has_media: bool) -> str:
    if language == "ar":
        return "ما قدرت اقرأ الملف بوضوح. أرسل صورة أوضح أو انسخ النص المهم." if has_media else "صار خلل تقني صغير. جرّب إرسال رسالتك مرة ثانية."
    if language == "uk":
        return "Сталася технічна помилка. Будь ласка, надішліть повідомлення ще раз."
    if language == "el":
        return "Παρουσιάστηκε τεχνικό πρόβλημα. Στείλε ξανά το μήνυμά σου."
    if language == "de":
        return "Es gab ein kleines technisches Problem. Bitte sende deine Nachricht erneut."
    return "A small technical issue occurred. Please send your message again."


def _deletion_confirmation(language: str) -> str:
    return {
        "ar": "تم حذف بياناتك المحفوظة.",
        "uk": "Збережені дані видалено.",
        "el": "Τα αποθηκευμένα δεδομένα σου διαγράφηκαν.",
        "de": "Deine gespeicherten Daten wurden gelöscht.",
        "en": "Your saved data has been deleted.",
    }.get(language, "Your saved data has been deleted.")


async def process_incoming(message: IncomingMessage) -> None:
    language = "de"
    has_media = message.media_id is not None
    try:
        profile = store.get_user(message.sender)
        language = detect_language(message.text) if message.text.strip() else str(profile.get("preferred_language") or "de")
        lowered = message.text.casefold()
        if any(phrase in lowered for phrase in ("lösch meine daten", "daten löschen", "delete my data", "امسح بياناتي", "видали мої дані")):
            store.delete_user(message.sender)
            await send_whatsapp_message(message.sender, _deletion_confirmation(language))
            return

        name = extract_name(message.text)
        updates: dict[str, Any] = {
            "preferred_language": language,
            "last_seen": datetime.now(UTC).isoformat(),
            "last_message": message.text[:200],
        }
        if name:
            updates["first_name"] = name
        profile = store.update_user(message.sender, updates)
        history = store.recent_user_messages(message.sender)

        image_bytes: bytes | None = None
        if message.media_id:
            media_url = await get_media_url(message.media_id)
            image_bytes = await download_media_bytes(media_url)

        prompt = build_system_prompt(
            sender=message.sender,
            text=message.text,
            detected_language=language,
            profile=profile,
            history=history,
            has_image=has_media,
        )
        reply = await anyio.to_thread.run_sync(lambda: generate_reply(
            system_prompt=prompt,
            user_text=message.text,
            image_bytes=image_bytes,
            mime_type=message.mime_type,
        ))
        await send_whatsapp_message(message.sender, reply)
        store.update_message_status(message.message_id, "sent")
    except Exception:
        logger.exception("Message processing failed", extra={"message_id": message.message_id})
        try:
            store.update_message_status(message.message_id, "failed")
        except Exception:
            logger.exception("Unable to record failed message status", extra={"message_id": message.message_id})
        try:
            await send_whatsapp_message(message.sender, _safe_failure(language, has_media))
        except Exception:
            logger.exception("Unable to deliver failure message", extra={"message_id": message.message_id})


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "AmtHero24", "status": "online", "version": APP_VERSION}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": APP_VERSION, "model": GROQ_MODEL, "storage": "json"}


@app.get("/webhook")
async def verify_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
) -> PlainTextResponse:
    try:
        verify_token = required_env("VERIFY_TOKEN")
    except RuntimeError:
        logger.error("Webhook verification attempted without VERIFY_TOKEN configured")
        return PlainTextResponse("Verification unavailable", status_code=503)
    if hub_mode == "subscribe" and hub_verify_token == verify_token:
        return PlainTextResponse(hub_challenge or "")
    return PlainTextResponse("Verification failed", status_code=403)


@app.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        logger.warning("Ignoring malformed webhook payload")
        return JSONResponse({"status": "accepted"})

    for message in extract_incoming_messages(payload):
        try:
            claimed = store.claim_message(
                message.message_id,
                message.sender,
                message.text,
                message_type=message.message_type,
                media_id=message.media_id,
            )
        except Exception:
            logger.exception("Unable to claim webhook message", extra={"message_id": message.message_id})
            continue
        if claimed:
            background_tasks.add_task(process_incoming, message)
    return JSONResponse({"status": "accepted"})
