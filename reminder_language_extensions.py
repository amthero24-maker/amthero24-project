"""Current-turn language handling for conversational reminders.

The reminder fast path runs before the normal app path updates language state.
This layer makes the current text turn authoritative for reminder replies while
preserving the existing consent and session storage rules.
"""
from __future__ import annotations

import re
from typing import Any

import onboarding as onboarding_rules
import reminder_conversation_extensions as reminders
import sam_product_voice as sam_voice
import sam_conversation_voice as sam_conversation

core = reminders.core
base = reminders.base
_ORIGINAL_PROCESS_INCOMING = reminders.process_incoming
_ORIGINAL_EXTRACT_TITLE = reminders._extract_title
_ORIGINAL_IS_NAME_QUESTION = core.is_name_question
_INSTALLED = False


def _normalize_name_question(text: str) -> str:
    value = onboarding_rules._normalize(text)
    return re.sub(r"\s+", " ", value).strip()


def _is_user_name_question(text: str) -> bool:
    """Recognize natural variants asking for the user's saved name.

    Keep Sam-name questions (for example ``شو اسمك انت`` / ``Wie heißt du``)
    out of this path so they continue to the product identity layer.
    """
    if _ORIGINAL_IS_NAME_QUESTION(text):
        return True
    normalized = _normalize_name_question(text)
    variants = {
        "شو اسمي انا", "شو اسمي أنا", "بتعرف شو اسمي", "بتتذكر شو اسمي",
        "ما هو اسمي", "ما هو اسمي انا", "ما هو اسمي أنا",
        "wie heiße ich denn", "wie heisse ich denn", "weißt du wie ich heiße", "weisst du wie ich heisse",
        "what is my name again", "do you remember what my name is", "do you know what my name is",
        "як мене звати знову", "ти пам ятаєш як мене звати",
        "πως με λενε ξανα", "θυμασαι πως με λενε",
    }
    return normalized in {_normalize_name_question(item) for item in variants}


def _is_german_reminder_turn(text: str) -> bool:
    """Recognize short German reminder turns that generic language scoring can miss."""
    normalized = str(text or "").casefold()
    return bool(
        re.search(r"\berinnere\s+mich\b", normalized)
        or re.search(r"\bminuten?\b", normalized)
        or re.search(r"\bstunden?\b", normalized)
    )


def detect_turn_language(text: str, profile: dict[str, Any]) -> str:
    fallback = base._language(profile)
    if _is_german_reminder_turn(text):
        return "de"
    return core.detect_language(text, fallback) if str(text or "").strip() else fallback


def clean_english_reminder_title(text: str, title: str) -> str:
    """Drop the infinitive marker left after removing an English time phrase."""
    if re.match(r"^\s*remind\s+me\b", str(text or ""), flags=re.IGNORECASE):
        return re.sub(r"^\s*to\s+", "", str(title or ""), count=1, flags=re.IGNORECASE).strip()
    return str(title or "").strip()


def _extract_title(text: str) -> str:
    return clean_english_reminder_title(text, _ORIGINAL_EXTRACT_TITLE(text))


def prepare_turn_language(message: Any) -> str:
    profile = core.store.get_user(message.sender)
    language = detect_turn_language(message.text, profile)
    previous = base._language(profile)
    if language == previous or str(profile.get("onboarding_stage") or "") != "complete":
        return language

    updates: dict[str, Any] = {
        "session_language": language,
        "session_expires_at": core._session_expiry(),
    }
    if profile.get("memory_consent") == "granted":
        updates["preferred_language"] = language
    core.store.update_user(message.sender, updates)
    return language


def should_clarify_implicit_snooze(intent: Any, recent_count: int) -> bool:
    """Avoid guessing which delivered reminder a bare time-only request refers to."""
    return bool(
        intent is not None
        and getattr(intent, "action", "") == "snooze"
        and getattr(intent, "snooze_implicit", False)
        and getattr(intent, "position", None) is None
        and recent_count > 1
    )


async def process_incoming(message: Any) -> None:
    if message.message_type == "text" and str(message.text or "").strip():
        language = prepare_turn_language(message)
        profile = core.store.get_user(message.sender)
        if (
            str(profile.get("onboarding_stage") or "") == "complete"
            and profile.get("memory_consent") == "granted"
            and base.reminder_delivery_ready(message.sender)
        ):
            intent = reminders.detect_conversational_reminder_intent(message.text)
            if intent is not None and getattr(intent, "snooze_implicit", False):
                recent = base._repository().recent_deliveries(message.sender, limit=10)
                if should_clarify_implicit_snooze(intent, len(recent)) and intent.scheduled_at is not None:
                    core.store.update_user(message.sender, {
                        reminders._PENDING_AT: intent.scheduled_at.isoformat(),
                        "session_language": language,
                        "session_expires_at": core._session_expiry(),
                    })
                    await core._finish(
                        message.message_id,
                        reminders._question(language, "title"),
                        message.sender,
                    )
                    return
    await _ORIGINAL_PROCESS_INCOMING(message)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    reminders._extract_title = _extract_title
    reminders.process_incoming = process_incoming
    base.process_incoming = process_incoming
    core.process_incoming = process_incoming
    core.is_name_question = _is_user_name_question
    sam_voice.install(core)
    sam_conversation.install(core)
    _INSTALLED = True
