"""Production composition root for AmtHero24.

The stable webhook remains in ``app.py``. This module layers the relationship
and mission intelligence on top without duplicating the webhook implementation.
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

_ORIGINAL_PROCESS_INCOMING = core.process_incoming
_ORIGINAL_BUILD_SYSTEM_PROMPT = core.build_system_prompt
_ORIGINAL_DETECT_MISSION_INTENT = core.detect_mission_intent
_ORIGINAL_MISSION_TITLE = core.mission_title


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


async def process_incoming(message: core.IncomingMessage) -> None:
    """Persist explicit preferences and adapt the current session before routing."""
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


# Patch the globals resolved by app.receive_webhook at request time.
core.build_system_prompt = build_system_prompt
core.memory_summary_message = human_memory_summary
core._export_reply = human_export_reply
core.detect_mission_intent = detect_mission_intent
core.mission_title = mission_title
core.process_incoming = process_incoming

app = core.app
store = core.store
