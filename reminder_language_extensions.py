"""Current-turn language handling for conversational reminders.

The reminder fast path runs before the normal app path updates language state.
This layer makes the current text turn authoritative for reminder replies while
preserving the existing consent and session storage rules.
"""
from __future__ import annotations

import re
from typing import Any

import reminder_conversation_extensions as reminders

core = reminders.core
base = reminders.base
_ORIGINAL_PROCESS_INCOMING = reminders.process_incoming
_ORIGINAL_EXTRACT_TITLE = reminders._extract_title
_INSTALLED = False


def detect_turn_language(text: str, profile: dict[str, Any]) -> str:
    fallback = base._language(profile)
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


async def process_incoming(message: Any) -> None:
    if message.message_type == "text" and str(message.text or "").strip():
        prepare_turn_language(message)
    await _ORIGINAL_PROCESS_INCOMING(message)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    reminders._extract_title = _extract_title
    reminders.process_incoming = process_incoming
    base.process_incoming = process_incoming
    core.process_incoming = process_incoming
    _INSTALLED = True
