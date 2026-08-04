"""Conversational reminder UX layered over the certified reminder engine.

This layer accepts concise relative times, keeps conversational topics out of
reminder titles, and asks only one targeted follow-up when either the subject or
time is missing. Pending clarification is session-scoped and contains no document
content or hidden inference.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import reminder_extensions as base
import shared_drain_extensions as composed
from encryption_policy import reminder_encryption_ready
from reminder_engine import DEFAULT_TIMEZONE, ReminderIntent, resolve_reminder_schedule

core = composed.core
_ORIGINAL_PROCESS_INCOMING = base.process_incoming
_PENDING_AT = "pending_reminder_at"
_PENDING_TITLE = "pending_reminder_title"
_CONVERSATION_TOPICS = {
    "", "unknown", "identity", "capabilities", "languages", "reminders",
    "greeting", "greeting_1", "greeting_2", "greeting_3",
}


@dataclass(frozen=True)
class ConversationalReminderIntent:
    action: str
    scheduled_at: datetime | None = None
    lead_days: int | None = None
    title: str = ""
    exact_time: bool = False


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").casefold().strip()
    value = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", value)
    value = value.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا"}))
    value = re.sub(r"[؟،؛!?.,:;]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _local_now(now: datetime | None, timezone_name: str) -> datetime:
    return (now or datetime.now(UTC)).astimezone(ZoneInfo(timezone_name))


def _clock_hour(raw_hour: int, qualifier: str) -> int | None:
    if not 0 <= raw_hour <= 23:
        return None
    value = _normalize(qualifier)
    if any(token in value for token in ("مساء", "المسا", "ليل", "pm", "abends", "вечора", "βραδυ")):
        return raw_hour + 12 if 1 <= raw_hour <= 11 else raw_hour
    if any(token in value for token in ("صباح", "الصبح", "am", "morgens", "ранку", "πρωι")):
        return 0 if raw_hour == 12 else raw_hour
    return raw_hour


def _parse_relative_minutes(normalized: str, current: datetime) -> datetime | None:
    singular = re.search(r"(?:بعد|in|через|σε)\s+(?:دقيقة|minute|min|хвилину|λεπτο)\b", normalized)
    if singular:
        return current + timedelta(minutes=1)
    dual = re.search(r"(?:بعد|in|через|σε)\s+(?:دقيقتين|two minutes|zwei minuten|дві хвилини|δυο λεπτα)\b", normalized)
    if dual:
        return current + timedelta(minutes=2)
    match = re.search(
        r"(?:بعد|in|через|σε)\s+(\d{1,4})\s*(?:دقيقة|دقائق|دقايق|minuten?|minutes?|mins?|хвилин(?:и)?|λεπτα)",
        normalized,
    )
    if match:
        minutes = int(match.group(1))
        if 1 <= minutes <= 10080:
            return current + timedelta(minutes=minutes)
    return None


def _parse_relative_hours(normalized: str, current: datetime) -> datetime | None:
    singular = re.search(r"(?:بعد|in|через|σε)\s+(?:ساعة|hour|einer stunde|годину|μια ωρα)\b", normalized)
    if singular:
        return current + timedelta(hours=1)
    dual = re.search(r"(?:بعد|in|через|σε)\s+(?:ساعتين|two hours|zwei stunden|дві години|δυο ωρες)\b", normalized)
    if dual:
        return current + timedelta(hours=2)
    match = re.search(
        r"(?:بعد|in|через|σε)\s+(\d{1,3})\s*(?:ساعة|ساعات|stunden?|hours?|hrs?|годин(?:и)?|ωρες)",
        normalized,
    )
    if match:
        hours = int(match.group(1))
        if 1 <= hours <= 720:
            return current + timedelta(hours=hours)
    return None


def _parse_clock(text: str, normalized: str, current: datetime, timezone_name: str) -> datetime | None:
    match = re.search(
        r"(?:الساعة|ساعه|um|at|о|στις)?\s*(\d{1,2})(?::(\d{2}))?\s*"
        r"(صباحا|صباح|الصبح|مساء|المسا|ليلا|ليل|am|pm|morgens|abends|ранку|вечора|πρωι|βραδυ)?",
        normalized,
    )
    if not match or not any(token in normalized for token in ("الساعة", "ساعه", " um ", " at ", "صباح", "مساء", "الصبح", "المسا", "am", "pm")):
        return None
    raw_hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    hour = _clock_hour(raw_hour, match.group(3) or "")
    if hour is None or minute > 59:
        return None
    day = current.date()
    if any(token in normalized for token in ("بكرا", "غدا", "morgen", "tomorrow", "завтра", "αυριο")):
        day += timedelta(days=1)
    scheduled = datetime.combine(day, time(hour=hour, minute=minute), tzinfo=ZoneInfo(timezone_name))
    if scheduled <= current and not any(token in normalized for token in ("بكرا", "غدا", "morgen", "tomorrow", "завтра", "αυριο")):
        scheduled += timedelta(days=1)
    return scheduled


def _extract_title(text: str) -> str:
    value = " ".join(str(text or "").split()).strip()
    value = re.sub(
        r"^(?:ذكرني|ذكّرني|تذكرني|نبهني|نبّهني|erinnere mich|remind me|нагадай мені|θυμισε μου)\s*",
        "", value, flags=re.IGNORECASE,
    )
    time_patterns = (
        r"\bبعد\s+(?:دقيقة|دقيقتين|\d+\s*(?:دقيقة|دقائق|دقايق|ساعة|ساعات|ايام|أيام|يوم))\b",
        r"\b(?:بكرا|غدا|غداً|اليوم)\b",
        r"\b(?:الساعة|ساعه)\s*\d{1,2}(?::\d{2})?\s*(?:صباحا|صباح|الصبح|مساء|المسا|ليلا|ليل)?",
        r"\b(?:in\s+\d+\s*(?:minutes?|hours?|days?)|tomorrow|today\s+at\s+\d{1,2}(?::\d{2})?)\b",
        r"\b(?:morgen\s+um\s+\d{1,2}(?::\d{2})?|in\s+\d+\s*(?:minuten?|stunden?|tagen?))\b",
    )
    for pattern in time_patterns:
        value = re.sub(pattern, " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip(" -،.")
    return value[:180]


def detect_conversational_reminder_intent(
    text: str,
    *,
    now: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> ConversationalReminderIntent | None:
    base_intent = base.detect_reminder_intent(base._command_text(text), now=now, timezone_name=timezone_name)
    if base_intent is None:
        return None
    if base_intent.action != "create":
        return ConversationalReminderIntent(base_intent.action)

    normalized = _normalize(text)
    current = _local_now(now, timezone_name)
    scheduled = _parse_relative_minutes(normalized, current)
    exact = scheduled is not None
    if scheduled is None:
        scheduled = _parse_relative_hours(normalized, current)
        exact = scheduled is not None
    if scheduled is None:
        scheduled = _parse_clock(text, normalized, current, timezone_name)
        exact = scheduled is not None
    if scheduled is None:
        scheduled = base_intent.scheduled_at
    return ConversationalReminderIntent(
        "create",
        scheduled_at=scheduled,
        lead_days=base_intent.lead_days,
        title=_extract_title(text),
        exact_time=exact,
    )


def resolve_conversational_schedule(
    intent: ConversationalReminderIntent,
    mission: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> datetime | None:
    if intent.exact_time and intent.scheduled_at is not None:
        current = _local_now(now, timezone_name)
        scheduled = intent.scheduled_at
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=ZoneInfo(timezone_name))
        return scheduled.astimezone(UTC) if scheduled > current else None
    return resolve_reminder_schedule(
        ReminderIntent(intent.action, intent.scheduled_at, intent.lead_days),
        mission,
        now=now,
        timezone_name=timezone_name,
    )


def _question(language: str, missing: str) -> str:
    messages = {
        "ar": {
            "title": "تمام. شو بتحب ذكّرك فيه؟ اكتبها بكلمتين مثل: اتصل بالمكتب.",
            "time": "إيمتى أذكّرك؟ فيك تقول: بعد دقيقة، بعد ساعتين، اليوم الساعة 7، أو بكرا الصبح.",
        },
        "de": {
            "title": "Woran soll ich dich erinnern? Ein kurzer Satz reicht, zum Beispiel: beim Amt anrufen.",
            "time": "Wann soll ich dich erinnern? Zum Beispiel: in einer Minute, in zwei Stunden oder morgen um 9.",
        },
        "en": {
            "title": "What should I remind you about? A few words are enough, for example: call the office.",
            "time": "When should I remind you? You can say: in one minute, in two hours, or tomorrow at 9.",
        },
        "uk": {
            "title": "Про що нагадати? Достатньо кількох слів, наприклад: зателефонувати до установи.",
            "time": "Коли нагадати? Наприклад: через хвилину, через дві години або завтра о 9.",
        },
        "el": {
            "title": "Για τι να σου θυμίσω; Αρκούν λίγες λέξεις, π.χ. τηλεφώνησε στην υπηρεσία.",
            "time": "Πότε να σου θυμίσω; Π.χ. σε ένα λεπτό, σε δύο ώρες ή αύριο στις 9.",
        },
    }
    lang = language if language in messages else "de"
    return messages[lang][missing]


def _clear_pending(sender: str) -> None:
    core.store.remove_user_fields(sender, {_PENDING_AT, _PENDING_TITLE})


def _real_mission_title(mission: dict[str, Any] | None) -> str:
    title = str((mission or {}).get("title") or "").strip()
    return "" if _normalize(title) in _CONVERSATION_TOPICS else title


async def process_incoming(message: core.IncomingMessage) -> None:
    profile = core.store.get_user(message.sender)
    language = base._language(profile)
    stage = str(profile.get("onboarding_stage") or "")
    pending_at = str(profile.get(_PENDING_AT) or "").strip()
    pending_title = str(profile.get(_PENDING_TITLE) or "").strip()

    intent = None
    if message.message_type == "text":
        intent = detect_conversational_reminder_intent(message.text)
        if intent is None and (pending_at or pending_title):
            if pending_at and not pending_title:
                intent = ConversationalReminderIntent("create", scheduled_at=datetime.fromisoformat(pending_at), title=" ".join(message.text.split())[:180], exact_time=True)
            elif pending_title and not pending_at:
                parsed = detect_conversational_reminder_intent("ذكرني " + message.text)
                if parsed is not None:
                    intent = ConversationalReminderIntent("create", parsed.scheduled_at, parsed.lead_days, pending_title, parsed.exact_time)

    if intent is None or stage != "complete":
        await _ORIGINAL_PROCESS_INCOMING(message)
        return
    if profile.get("memory_consent") != "granted":
        await core._finish(message.message_id, core.memory_required_message(language), message.sender)
        return

    repository = base._repository()
    if intent.action == "list":
        await core._finish(message.message_id, base.reminder_list_message(language, repository.list(message.sender, active_only=True, limit=10)), message.sender)
        return
    if intent.action in {"cancel", "cancel_all"}:
        count = repository.cancel(message.sender, all_active=intent.action == "cancel_all")
        await core._finish(message.message_id, base.reminder_cancelled_message(language, count), message.sender)
        return
    if not reminder_encryption_ready():
        await core._finish(message.message_id, base.reminder_unavailable_message(language), message.sender)
        return

    mission = core._hero_memory().get_latest_mission(message.sender)
    scheduled_at = resolve_conversational_schedule(intent, mission)
    title = intent.title or _real_mission_title(mission)

    if scheduled_at is None:
        core.store.update_user(message.sender, {
            _PENDING_TITLE: title,
            "session_language": language,
            "session_expires_at": core._session_expiry(),
        })
        await core._finish(message.message_id, _question(language, "time"), message.sender)
        return
    if not title:
        core.store.update_user(message.sender, {
            _PENDING_AT: scheduled_at.isoformat(),
            "session_language": language,
            "session_expires_at": core._session_expiry(),
        })
        await core._finish(message.message_id, _question(language, "title"), message.sender)
        return

    _clear_pending(message.sender)
    core.store.update_user(message.sender, {
        "last_seen": core._now().isoformat(),
        "session_language": language,
        "session_topic": "reminders",
        "session_expires_at": core._session_expiry(),
    })
    reminder = repository.create(
        message.sender,
        title=title,
        scheduled_at=scheduled_at,
        language=language,
        mission_id=str((mission or {}).get("mission_id") or ""),
    )
    await core._finish(message.message_id, base.reminder_created_message(language, reminder), message.sender)


base.process_incoming = process_incoming
core.process_incoming = process_incoming
app = composed.app
store = composed.store
