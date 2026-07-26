"""AmtHero24 FastAPI entrypoint and WhatsApp webhook."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import anyio
from fastapi import BackgroundTasks, FastAPI, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from config import APP_VERSION, DATA_STORE_PATH, GROQ_MODEL, required_env
from conversation_intelligence import build_effective_user_text, detect_language, extract_city, infer_topic
from data_store import JsonDataStore
from groq_client import generate_reply
from onboarding import (
    MEMORY_CONSENT_VERSION,
    ask_name_message,
    consent_decision,
    consent_declined_message,
    consent_granted_message,
    consent_prompt,
    is_enable_memory_request,
    is_memory_summary_request,
    is_simple_greeting,
    memory_summary_message,
    welcome_message,
)
from product_knowledge import product_answer
from prompts import build_system_prompt
from whatsapp import download_media_bytes, get_media_url, send_whatsapp_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("amthero24")

app = FastAPI(title="AmtHero24", version=APP_VERSION)
store = JsonDataStore(DATA_STORE_PATH)

_LONG_TERM_MEMORY_FIELDS = {
    "first_name", "city", "preferred_language", "current_topic", "last_assistant_reply",
    "conversation_summary", "communication_style",
}


@dataclass(frozen=True)
class IncomingMessage:
    message_id: str
    sender: str
    text: str
    message_type: str
    media_id: str | None = None
    mime_type: str = "application/octet-stream"


def _now() -> datetime:
    return datetime.now(UTC)


def _session_expiry() -> str:
    return (_now() + timedelta(hours=24)).isoformat()


def _is_name_only(text: str, name: str) -> bool:
    cleaned = (text or "").strip()
    return bool(name and len(cleaned) <= 50 and len(cleaned.split()) <= 4 and not re.search(r"[?!؟]", cleaned))


def extract_name(text: str) -> str:
    cleaned = (text or "").strip()
    patterns = (
        r"(?:اسمي|أنا اسمي|انا اسمي)\s+([\u0600-\u06FFA-Za-zÄÖÜäöüß-]{2,30})",
        r"(?:ich heiße|mein name ist)\s+([A-Za-zÄÖÜäöüß-]{2,30})",
        r"(?:my name is)\s+([A-Za-z-]{2,30})",
        r"(?:мене звати)\s+([\u0400-\u04FF-]{2,30})",
        r"(?:με λένε)\s+([\u0370-\u03FF-]{2,30})",
    )
    for pattern in patterns:
        match = re.search(pattern, cleaned, re.IGNORECASE)
        if match:
            return match.group(1)
    if 1 <= len(cleaned.split()) <= 2 and len(cleaned) <= 40 and not re.search(r"[?!؟]", cleaned):
        if re.fullmatch(r"[\u0600-\u06FFA-Za-zÄÖÜäöüß\u0400-\u04FF\u0370-\u03FF' -]+", cleaned):
            ignored = {
                "مرحبا", "أهلا", "اهلا", "سلام", "هلا", "تمام", "نعم", "لا", "شكرا", "مساعدة",
                "بالعربي", "بالعربية", "بالألماني", "بالانجليزي", "جديد", "جديدة", "هون", "هنا",
                "تاني", "ثاني", "كمان", "شو كمان", "شو بتقدم", "شو بتعمل", "شو اللغات",
                "عندي مشكلة", "بدي مساعدة", "مو هلق", "مش هلق", "بدون ذاكرة",
                "hallo", "hi", "hilfe", "danke", "ja", "nein", "okay", "ok", "deutsch", "english",
                "hello", "help", "thanks", "yes", "no", "привіт", "так", "ні", "дякую", "βοήθεια", "γεια", "ναι", "όχι",
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
    for entry in payload.get("entry", []) if isinstance(payload.get("entry", []), list) else []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes", []) if isinstance(entry.get("changes", []), list) else []:
            value = change.get("value", {}) if isinstance(change, dict) else {}
            messages = value.get("messages", []) if isinstance(value, dict) else []
            for raw in messages if isinstance(messages, list) else []:
                parsed = _message_from_payload(raw) if isinstance(raw, dict) else None
                if parsed:
                    extracted.append(parsed)
    return extracted


def extract_text_messages(payload: Any) -> list[tuple[str, str, str]]:
    return [(item.message_id, item.sender, item.text) for item in extract_incoming_messages(payload) if item.message_type in {"text", "button", "interactive"}]


def _safe_failure(language: str, has_media: bool) -> str:
    messages = {
        "ar": "ما قدرت اقرأ الملف بوضوح. ابعت صورة أوضح أو قصّ الجزء المهم." if has_media else "صار خلل تقني صغير. ابعت رسالتك مرة ثانية.",
        "uk": "Сталася технічна помилка. Будь ласка, надішліть повідомлення ще раз.",
        "el": "Παρουσιάστηκε τεχνικό πρόβλημα. Στείλε ξανά το μήνυμά σου.",
        "de": "Es gab ein kleines technisches Problem. Bitte sende deine Nachricht erneut.",
        "en": "A small technical issue occurred. Please send your message again.",
    }
    return messages.get(language, messages["de"])


def _deletion_confirmation(language: str) -> str:
    return {
        "ar": "تم حذف بياناتك المحفوظة.", "uk": "Збережені дані видалено.",
        "el": "Τα αποθηκευμένα δεδομένα σου διαγράφηκαν.", "de": "Deine gespeicherten Daten wurden gelöscht.",
        "en": "Your saved data has been deleted.",
    }.get(language, "Your saved data has been deleted.")


async def _finish(message_id: str, reply: str, sender: str) -> None:
    await send_whatsapp_message(sender, reply)
    store.update_message_status(message_id, "sent")


async def process_incoming(message: IncomingMessage) -> None:
    language = "de"
    has_media = message.media_id is not None
    try:
        store.cleanup_expired()
        profile = store.get_user(message.sender)
        memory_enabled = profile.get("memory_consent") == "granted"
        previous_language = str(
            profile.get("preferred_language") if memory_enabled else profile.get("session_language")
            or profile.get("preferred_language")
            or "de"
        )
        previous_topic = str(
            profile.get("current_topic") if memory_enabled else profile.get("session_topic")
            or profile.get("current_topic")
            or ""
        )
        language = detect_language(message.text, previous_language) if message.text.strip() else previous_language
        lowered = message.text.casefold()

        if any(phrase in lowered for phrase in ("lösch meine daten", "daten löschen", "delete my data", "امسح بياناتي", "احذف بياناتي", "видали мої дані")):
            store.delete_user(message.sender)
            await _finish(message.message_id, _deletion_confirmation(language), message.sender)
            return

        if is_memory_summary_request(message.text):
            await _finish(message.message_id, memory_summary_message(language, profile), message.sender)
            return

        if is_enable_memory_request(message.text) and not memory_enabled:
            pending_name = str(profile.get("pending_name") or profile.get("first_name") or "").strip()
            stage = "awaiting_consent" if pending_name else "awaiting_name"
            updates: dict[str, Any] = {
                "onboarding_stage": stage,
                "session_language": language,
                "session_expires_at": _session_expiry(),
            }
            if pending_name:
                updates["pending_name"] = pending_name
                updates["pending_name_expires_at"] = _session_expiry()
            profile = store.update_user(message.sender, updates)
            reply = consent_prompt(language, pending_name) if pending_name else ask_name_message(language)
            await _finish(message.message_id, reply, message.sender)
            return

        extracted_name = extract_name(message.text)
        is_new_user = not profile
        stage = str(profile.get("onboarding_stage") or "")

        if is_new_user:
            onboarding_updates: dict[str, Any] = {
                "intro_sent_at": _now().isoformat(),
                "onboarding_stage": "awaiting_consent" if extracted_name else "awaiting_name",
                "session_language": language,
                "session_expires_at": _session_expiry(),
            }
            if extracted_name:
                onboarding_updates["pending_name"] = extracted_name
                onboarding_updates["pending_name_expires_at"] = _session_expiry()
            profile = store.update_user(message.sender, onboarding_updates)
            await send_whatsapp_message(message.sender, welcome_message(language, extracted_name))
            if extracted_name:
                await send_whatsapp_message(message.sender, consent_prompt(language, extracted_name))
            if is_simple_greeting(message.text) or _is_name_only(message.text, extracted_name):
                store.update_message_status(message.message_id, "sent")
                return
            stage = str(profile.get("onboarding_stage") or "")

        elif not profile.get("memory_consent") and not profile.get("intro_sent_at"):
            # One-time consent repair for profiles created by older builds.
            pending_name = str(profile.get("first_name") or "").strip()
            repair_updates: dict[str, Any] = {
                "intro_sent_at": _now().isoformat(),
                "onboarding_stage": "awaiting_consent" if pending_name else "awaiting_name",
                "session_language": language,
                "session_expires_at": _session_expiry(),
            }
            if pending_name:
                repair_updates["pending_name"] = pending_name
                repair_updates["pending_name_expires_at"] = _session_expiry()
            profile = store.update_user(message.sender, repair_updates)
            await send_whatsapp_message(
                message.sender,
                consent_prompt(language, pending_name) if pending_name else ask_name_message(language),
            )
            stage = str(profile.get("onboarding_stage") or "")
            if is_simple_greeting(message.text):
                store.update_message_status(message.message_id, "sent")
                return

        if stage == "awaiting_name" and extracted_name:
            profile = store.update_user(message.sender, {
                "pending_name": extracted_name,
                "pending_name_expires_at": _session_expiry(),
                "onboarding_stage": "awaiting_consent",
                "session_language": language,
                "session_expires_at": _session_expiry(),
            })
            await send_whatsapp_message(message.sender, consent_prompt(language, extracted_name))
            if _is_name_only(message.text, extracted_name):
                store.update_message_status(message.message_id, "sent")
                return
            stage = "awaiting_consent"

        if stage == "awaiting_consent":
            decision = consent_decision(message.text)
            if decision is not None:
                pending_name = str(profile.get("pending_name") or "").strip()
                consent_updates: dict[str, Any] = {
                    "memory_consent": "granted" if decision else "declined",
                    "memory_consent_at": _now().isoformat(),
                    "memory_consent_version": MEMORY_CONSENT_VERSION,
                    "onboarding_stage": "complete",
                    "session_language": language,
                    "session_expires_at": _session_expiry(),
                }
                if decision:
                    consent_updates["preferred_language"] = language
                    if pending_name:
                        consent_updates["first_name"] = pending_name
                    profile = store.update_user(message.sender, consent_updates)
                    profile = store.remove_user_fields(message.sender, {"pending_name", "pending_name_expires_at"})
                    await _finish(message.message_id, consent_granted_message(language, pending_name), message.sender)
                else:
                    store.update_user(message.sender, consent_updates)
                    profile = store.remove_user_fields(
                        message.sender,
                        _LONG_TERM_MEMORY_FIELDS | {"pending_name", "pending_name_expires_at"},
                    )
                    await _finish(message.message_id, consent_declined_message(language), message.sender)
                return

        profile = store.get_user(message.sender)
        memory_enabled = profile.get("memory_consent") == "granted"
        previous_topic = str(profile.get("current_topic") if memory_enabled else profile.get("session_topic") or previous_topic)
        authoritative = product_answer(message.text, language, previous_topic)
        effective_text = build_effective_user_text(message.text, profile)
        city = extract_city(message.text)
        inferred_topic = infer_topic(message.text, previous_topic)
        topic = authoritative[1] if authoritative else (inferred_topic or ("document" if has_media else previous_topic))

        operational_updates: dict[str, Any] = {
            "session_language": language,
            "session_topic": topic,
            "session_expires_at": _session_expiry(),
            "last_seen": _now().isoformat(),
        }
        if memory_enabled:
            operational_updates.update({
                "preferred_language": language,
                "last_message": message.text[:200],
                "last_message_type": message.message_type,
                "current_topic": topic,
            })
            if extracted_name:
                operational_updates["first_name"] = extracted_name
            if city:
                operational_updates["city"] = city
        profile = store.update_user(message.sender, operational_updates)

        if authoritative:
            reply = authoritative[0]
            await send_whatsapp_message(message.sender, reply)
            response_updates: dict[str, Any] = {
                "session_last_reply": reply,
                "session_topic": topic,
                "session_expires_at": _session_expiry(),
            }
            if memory_enabled:
                response_updates.update({
                    "last_assistant_reply": reply,
                    "conversation_summary": f"Language={language}; city={profile.get('city', '')}; topic={topic}; authoritative product answer",
                })
            store.update_user(message.sender, response_updates)
            store.update_message_status(message.message_id, "sent")
            return

        history = store.recent_user_messages(message.sender, limit=6)
        image_bytes: bytes | None = None
        if message.media_id:
            media_url = await get_media_url(message.media_id)
            image_bytes = await download_media_bytes(media_url)
            if not effective_text.strip():
                effective_text = "Explain this document clearly in the user's preferred language. State what it means, what matters, and the next practical step."

        prompt_profile = dict(profile)
        prompt_profile.setdefault("preferred_language", language)
        prompt_profile.setdefault("current_topic", topic)
        prompt_profile.setdefault("last_assistant_reply", str(profile.get("session_last_reply") or ""))
        prompt = build_system_prompt(
            sender=message.sender,
            text=effective_text,
            detected_language=language,
            profile=prompt_profile,
            history=history,
            has_image=has_media,
        )
        reply = await anyio.to_thread.run_sync(lambda: generate_reply(
            system_prompt=prompt,
            user_text=effective_text,
            image_bytes=image_bytes,
            mime_type=message.mime_type,
        ))
        await send_whatsapp_message(message.sender, reply)
        response_updates = {
            "session_last_reply": reply,
            "session_topic": topic,
            "session_expires_at": _session_expiry(),
        }
        if memory_enabled:
            response_updates.update({
                "last_assistant_reply": reply,
                "conversation_summary": f"Language={language}; city={profile.get('city', '')}; topic={topic}; latest request={message.text[:180]}",
            })
        store.update_user(message.sender, response_updates)
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
            claimed = store.claim_message(message.message_id, message.sender, message.text, message_type=message.message_type, media_id=message.media_id)
        except Exception:
            logger.exception("Unable to claim webhook message", extra={"message_id": message.message_id})
            continue
        if claimed:
            background_tasks.add_task(process_incoming, message)
    return JSONResponse({"status": "accepted"})
