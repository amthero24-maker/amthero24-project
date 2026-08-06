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
import reminder_pending_storage  # noqa: F401
import shared_drain_extensions as composed
from reminder_engine import DEFAULT_TIMEZONE, ReminderIntent, resolve_reminder_schedule

core = composed.core
_ORIGINAL_PROCESS_INCOMING = base.process_incoming
_PENDING_AT = "pending_reminder_at"
_PENDING_TITLE = "pending_reminder_title"
_PENDING_RECURRENCE_DAYS = "pending_reminder_recurrence_days"
_PENDING_RECURRENCE_COUNT = "pending_reminder_recurrence_count"
_PENDING_WEEKDAYS_ONLY = "pending_reminder_weekdays_only"
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
    position: int | None = None
    positions: tuple[int, ...] = ()
    recurrence_days: int | None = None
    recurrence_count: int | None = None
    weekdays_only: bool = False
    recurrence_stop: bool = False


_RESCHEDULE_MARKERS = (
    "اجل التذكير", "أجل التذكير", "أجّل التذكير", "اخر التذكير", "أخر التذكير",
    "غير موعد التذكير", "غيّر موعد التذكير", "verschiebe die erinnerung",
    "erinnerung verschieben", "snooze reminder", "postpone reminder", "move reminder",
    "перенеси нагадування", "μεταφερε την υπενθυμιση",
)

_RECURRENCE_STOP_MARKERS = (
    "وقف التكرار", "وقف تكرار", "اوقف التكرار", "اوقف تكرار", "خليه مرة واحدة", "خليها مرة واحدة",
    "stop repeating", "make it one time", "wiederholung stoppen",
    "зупини повторення", "σταματησε την επαναληψη",
)
_RECURRENCE_UPDATE_MARKERS = (
    "خلي التذكير", "خلي تذكير", "غير تكرار", "غيّر تكرار",
    "change reminder recurrence", "make reminder", "erinnerung wiederholen",
    "зміни повторення", "αλλαξε την επαναληψη",
)
_WEEKDAY_RECURRENCE_MARKERS = (
    "ايام العمل", "ايام الدوام", "كل يوم ما عدا السبت والاحد", "كل يوم عدا السبت والاحد",
    "weekdays", "weekday only", "every workday", "workdays", "excluding weekends",
    "every day except weekends", "every day except saturday and sunday",
    "werktags", "jeden werktag", "nur werktags", "montag bis freitag",
    "ausser samstag und sonntag", "außer samstag und sonntag",
    "по буднях", "у робочі дні", "щодня крім суботи та неділі",
    "εργασιμες ημερες", "εργάσιμες ημέρες", "καθημερινες", "καθημερινές",
    "καθε μερα εκτος σαββατου και κυριακης",
)

_ARABIC_QUANTITIES = {
    "ثلاث": 3, "ثلاثة": 3, "اربع": 4, "اربعة": 4,
    "خمس": 5, "خمسة": 5, "ست": 6, "ستة": 6,
    "سبع": 7, "سبعة": 7, "ثمان": 8, "ثمانية": 8,
    "تسع": 9, "تسعة": 9, "عشر": 10, "عشرة": 10,
}
_ARABIC_QUANTITY_PATTERN = "|".join(sorted(_ARABIC_QUANTITIES, key=len, reverse=True))


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
    word_match = re.search(
        rf"بعد\s+({_ARABIC_QUANTITY_PATTERN})\s*(?:دقيقة|دقائق|دقايق)\b",
        normalized,
    )
    if word_match:
        return current + timedelta(minutes=_ARABIC_QUANTITIES[word_match.group(1)])
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
    word_match = re.search(
        rf"بعد\s+({_ARABIC_QUANTITY_PATTERN})\s*(?:ساعة|ساعات)\b",
        normalized,
    )
    if word_match:
        return current + timedelta(hours=_ARABIC_QUANTITIES[word_match.group(1)])
    return None


def _parse_reschedule_position(normalized: str) -> int | None:
    match = re.search(r"(?:التذكير|erinnerung|reminder|нагадування|υπενθυμιση)\s*(\d{1,2})\b", normalized)
    if match:
        return int(match.group(1))
    words = {"الاول": 1, "الأول": 1, "الثاني": 2, "الثالث": 3, "first": 1, "second": 2, "third": 3}
    return next((number for word, number in words.items() if word in normalized), None)


def _parse_cancel_positions(normalized: str) -> tuple[int, ...]:
    values = {int(value) for value in re.findall(r"\b(\d{1,2})\b", normalized)}
    ordinals = {
        "الاول": 1, "الثاني": 2, "الثالث": 3, "الرابع": 4, "الخامس": 5,
        "السادس": 6, "السابع": 7, "الثامن": 8, "التاسع": 9, "العاشر": 10,
        "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
        "erste": 1, "ersten": 1, "zweite": 2, "zweiten": 2, "dritte": 3, "dritten": 3,
    }
    values.update(number for word, number in ordinals.items() if word in normalized)
    return tuple(sorted(value for value in values if 1 <= value <= 30))


def _parse_reschedule_time(text: str, normalized: str, current: datetime, timezone_name: str) -> datetime | None:
    scheduled = _parse_relative_minutes(normalized, current) or _parse_relative_hours(normalized, current)
    if scheduled is not None:
        return scheduled
    compact = re.search(r"(\d{1,4})\s*(?:دقيقة|دقائق|دقايق|minuten?|minutes?|mins?|хвилин(?:и)?|λεπτα)", normalized)
    if compact and 1 <= int(compact.group(1)) <= 10080:
        return current + timedelta(minutes=int(compact.group(1)))
    compact = re.search(r"(\d{1,3})\s*(?:ساعة|ساعات|stunden?|hours?|hrs?|годин(?:и)?|ωρες)", normalized)
    if compact and 1 <= int(compact.group(1)) <= 720:
        return current + timedelta(hours=int(compact.group(1)))
    scheduled = _parse_clock(text, normalized, current, timezone_name)
    if scheduled is not None:
        return scheduled
    parsed = base.detect_reminder_intent("ذكرني " + text, now=current, timezone_name=timezone_name)
    return parsed.scheduled_at if parsed is not None else None


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
        rf"\bبعد\s+(?:دقيقة|دقيقتين|ساعة|ساعتين|(?:\d+|{_ARABIC_QUANTITY_PATTERN})\s*(?:دقيقة|دقائق|دقايق|ساعة|ساعات|ايام|أيام|يوم))\b",
        r"\b(?:قبلها|قبل الموعد|قبل المهلة)\s+(?:ب?يومين|ب?يوم|\d+\s*(?:يوم|ايام|أيام))\b",
        r"\b(?:بكرا|غدا|غداً|اليوم)\b",
        r"\b(?:الساعة|ساعه)\s*\d{1,2}(?::\d{2})?\s*(?:صباحا|صباح|الصبح|مساء|المسا|ليلا|ليل)?",
        r"\b(?:in\s+\d+\s*(?:minutes?|hours?|days?)|tomorrow|today\s+at\s+\d{1,2}(?::\d{2})?)\b",
        r"\b(?:morgen\s+um\s+\d{1,2}(?::\d{2})?|in\s+\d+\s*(?:minuten?|stunden?|tagen?))\b",
        r"\b(?:أيام|ايام)\s+(?:العمل|الدوام)(?:\s+فقط)?\b",
        r"\bكل\s+يوم\s+(?:ما\s+عدا|عدا)\s+السبت\s+و(?:الأحد|الاحد)\b",
        r"\b(?:every\s+workday|weekdays?(?:\s+only)?|workdays?|excluding\s+weekends|every\s+day\s+except\s+(?:weekends|saturday\s+and\s+sunday))\b",
        r"\b(?:jeden\s+werktag|nur\s+werktags?|werktags?|montag\s+bis\s+freitag|außer\s+samstag\s+und\s+sonntag|ausser\s+samstag\s+und\s+sonntag)\b",
        r"\b(?:по\s+буднях|у\s+робочі\s+дні|щодня\s+крім\s+суботи\s+та\s+неділі)\b",
        r"\b(?:εργάσιμες\s+ημέρες|εργασιμες\s+ημερες|καθημερινές|καθημερινες|κάθε\s+μέρα\s+εκτός\s+σαββάτου\s+και\s+κυριακής|καθε\s+μερα\s+εκτος\s+σαββατου\s+και\s+κυριακης)\b",
        r"\b(?:كل\s+(?:يوم|اسبوع|أسبوع)|يوميا|يوميًا|اسبوعيا|أسبوعيًا)\b",
        r"\bلمدة\s+\d{1,3}\s*(?:مرات?|يوم|ايام|أيام|اسبوع|أسابيع|اسابيع)\b",
        r"\b(?:every\s+(?:day|week)|daily|weekly)\b",
        r"\bfor\s+\d{1,3}\s*(?:times?|occurrences?|days?|weeks?)\b",
        r"\b(?:jeden\s+tag|jede\s+woche|täglich|wöchentlich)\b",
        r"\bfür\s+\d{1,3}\s*(?:mal|tage?|wochen?)\b",
        r"\b(?:щодня|кожного\s+дня|щотижня|кожного\s+тижня)\b",
        r"\bпротягом\s+\d{1,3}\s*(?:разів|днів|тижнів)\b",
        r"\bзавтра\b",
        r"\b(?:καθε\s+μερα|καθε\s+εβδομαδα)\b",
        r"\bγια\s+\d{1,3}\s*(?:φορες|φορές|ημερες|εβδομαδες)\b",
        r"\bαυριο\b",
    )
    for pattern in time_patterns:
        value = re.sub(pattern, " ", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip(" -،.")
    return value[:180]


def _weekdays_only(normalized: str) -> bool:
    return any(_normalize(marker) in normalized for marker in _WEEKDAY_RECURRENCE_MARKERS)


def _parse_recurrence(normalized: str) -> tuple[int | None, int | None, bool]:
    weekday_schedule = _weekdays_only(normalized)
    daily = weekday_schedule or any(token in normalized for token in (
        "كل يوم", "يوميا", "every day", "daily", "jeden tag", "taglich", "täglich",
        "щодня", "кожного дня", "καθε μερα",
    ))
    weekly = any(token in normalized for token in (
        "كل اسبوع", "اسبوعيا", "every week", "weekly", "jede woche", "wochentlich", "wöchentlich",
        "щотижня", "кожного тижня", "καθε εβδομαδα",
    ))
    if not daily and not weekly:
        return None, None, False
    days = 1 if daily else 7
    unit = (
        r"(?:مرات?|يوم|ايام|اسبوع|اسابيع|times?|occurrences?|days?|weeks?|mal|tage?|"
        r"wochen?|разів|днів|тижнів|φορεσ|φορές|ημερεσ|εβδομαδεσ)"
    )
    match = re.search(rf"(?:لمدة|for|fur|für|протягом|για)\s+(\d{{1,3}})\s*{unit}\b", normalized)
    if not match:
        return days, None, weekday_schedule
    count = int(match.group(1))
    return days, count if 2 <= count <= 365 else None, weekday_schedule


def _parse_recurrence_count_reply(text: str, recurrence_days: int) -> int | None:
    normalized = _normalize(text)
    match = re.fullmatch(
        r"(?:لمدة|for|fur|für|протягом|για)?\s*(\d{1,3})\s*"
        r"(?:مرات?|ايام?|اسابيع?|times?|occurrences?|days?|weeks?|mal|tage?|wochen?|"
        r"разів|днів|тижнів|φορεσ|φορές|ημερεσ|εβδομαδεσ)?",
        normalized,
    )
    if not match:
        return None
    count = int(match.group(1))
    return count if recurrence_days in {1, 7} and 2 <= count <= 365 else None


def detect_conversational_reminder_intent(
    text: str,
    *,
    now: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> ConversationalReminderIntent | None:
    normalized = _normalize(text)
    current = _local_now(now, timezone_name)
    if any(_normalize(marker) in normalized for marker in _RECURRENCE_STOP_MARKERS):
        return ConversationalReminderIntent(
            "recurrence_update", position=_parse_reschedule_position(normalized), recurrence_stop=True,
        )
    recurrence_days, recurrence_count, weekdays_only = _parse_recurrence(normalized)
    if recurrence_days is not None and any(
        _normalize(marker) in normalized for marker in _RECURRENCE_UPDATE_MARKERS
    ):
        return ConversationalReminderIntent(
            "recurrence_update",
            position=_parse_reschedule_position(normalized),
            recurrence_days=recurrence_days,
            recurrence_count=recurrence_count,
            weekdays_only=weekdays_only,
        )
    if any(_normalize(marker) in normalized for marker in _RESCHEDULE_MARKERS):
        return ConversationalReminderIntent(
            "reschedule",
            scheduled_at=_parse_reschedule_time(text, normalized, current, timezone_name),
            exact_time=True,
            position=_parse_reschedule_position(normalized),
        )
    base_intent = base.detect_reminder_intent(base._command_text(text), now=now, timezone_name=timezone_name)
    if base_intent is None:
        return None
    if base_intent.action != "create":
        return ConversationalReminderIntent(
            base_intent.action,
            positions=_parse_cancel_positions(normalized) if base_intent.action == "cancel" else (),
        )

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
    recurrence_days, recurrence_count, weekdays_only = _parse_recurrence(normalized)
    return ConversationalReminderIntent(
        "create",
        scheduled_at=scheduled,
        lead_days=base_intent.lead_days,
        title=_extract_title(text),
        exact_time=exact,
        recurrence_days=recurrence_days,
        recurrence_count=recurrence_count,
        weekdays_only=weekdays_only,
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
            "recurrence": "لكم مرة أكرر التذكير؟ مثال: كل يوم الساعة 8 لمدة 7 أيام.",
        },
        "de": {
            "title": "Woran soll ich dich erinnern? Ein kurzer Satz reicht, zum Beispiel: beim Amt anrufen.",
            "time": "Wann soll ich dich erinnern? Zum Beispiel: in einer Minute, in zwei Stunden oder morgen um 9.",
            "recurrence": "Wie oft soll ich erinnern? Zum Beispiel: jeden Tag um 8 Uhr für 7 Tage.",
        },
        "en": {
            "title": "What should I remind you about? A few words are enough, for example: call the office.",
            "time": "When should I remind you? You can say: in one minute, in two hours, or tomorrow at 9.",
            "recurrence": "How many times should it repeat? For example: every day at 8 for 7 days.",
        },
        "uk": {
            "title": "Про що нагадати? Достатньо кількох слів, наприклад: зателефонувати до установи.",
            "time": "Коли нагадати? Наприклад: через хвилину, через дві години або завтра о 9.",
            "recurrence": "Скільки разів повторити? Наприклад: щодня о 8 протягом 7 днів.",
        },
        "el": {
            "title": "Για τι να σου θυμίσω; Αρκούν λίγες λέξεις, π.χ. τηλεφώνησε στην υπηρεσία.",
            "time": "Πότε να σου θυμίσω; Π.χ. σε ένα λεπτό, σε δύο ώρες ή αύριο στις 9.",
            "recurrence": "Πόσες φορές να επαναληφθεί; Π.χ. κάθε μέρα στις 8 για 7 ημέρες.",
        },
    }
    lang = language if language in messages else "de"
    return messages[lang][missing]


def _clear_pending(sender: str) -> None:
    core.store.remove_user_fields(sender, {
        _PENDING_AT, _PENDING_TITLE, _PENDING_RECURRENCE_DAYS,
        _PENDING_RECURRENCE_COUNT, _PENDING_WEEKDAYS_ONLY,
    })


def _real_mission_title(mission: dict[str, Any] | None) -> str:
    title = str((mission or {}).get("title") or "").strip()
    return "" if _normalize(title) in _CONVERSATION_TOPICS else title


async def process_incoming(message: core.IncomingMessage) -> None:
    profile = core.store.get_user(message.sender)
    language = base._language(profile)
    stage = str(profile.get("onboarding_stage") or "")
    pending_at = str(profile.get(_PENDING_AT) or "").strip()
    pending_title = str(profile.get(_PENDING_TITLE) or "").strip()
    pending_recurrence_days = int(profile.get(_PENDING_RECURRENCE_DAYS) or 0)
    pending_recurrence_count = int(profile.get(_PENDING_RECURRENCE_COUNT) or 0)
    pending_weekdays_only = str(profile.get(_PENDING_WEEKDAYS_ONLY) or "") == "1"

    intent = None
    if message.message_type == "text":
        intent = detect_conversational_reminder_intent(message.text)
        if (
            intent is None and pending_at and pending_title
            and pending_recurrence_days in {1, 7} and pending_recurrence_count == 0
        ):
            count = _parse_recurrence_count_reply(message.text, pending_recurrence_days)
            if count is not None:
                intent = ConversationalReminderIntent(
                    "create",
                    scheduled_at=datetime.fromisoformat(pending_at),
                    title=pending_title,
                    exact_time=True,
                    recurrence_days=pending_recurrence_days,
                    recurrence_count=count,
                    weekdays_only=pending_weekdays_only,
                )
            else:
                intent = ConversationalReminderIntent("recurrence_clarification")
        if intent is None and (pending_at or pending_title):
            if pending_at and not pending_title:
                intent = ConversationalReminderIntent(
                    "create", scheduled_at=datetime.fromisoformat(pending_at),
                    title=" ".join(message.text.split())[:180], exact_time=True,
                    recurrence_days=pending_recurrence_days or None,
                    recurrence_count=pending_recurrence_count or None,
                    weekdays_only=pending_weekdays_only,
                )
            elif pending_title and not pending_at:
                parsed = detect_conversational_reminder_intent("ذكرني " + message.text)
                if parsed is not None:
                    intent = ConversationalReminderIntent(
                        "create", parsed.scheduled_at, parsed.lead_days, pending_title,
                        parsed.exact_time, recurrence_days=pending_recurrence_days or parsed.recurrence_days,
                        recurrence_count=pending_recurrence_count or parsed.recurrence_count,
                        weekdays_only=pending_weekdays_only or parsed.weekdays_only,
                    )

    if intent is None or stage != "complete":
        await _ORIGINAL_PROCESS_INCOMING(message)
        return
    if profile.get("memory_consent") != "granted":
        await core._finish(message.message_id, core.memory_required_message(language), message.sender)
        return

    repository = base._repository()
    if intent.action == "recurrence_clarification":
        await core._finish(message.message_id, _question(language, "recurrence"), message.sender)
        return
    if intent.action == "recurrence_update":
        if not intent.recurrence_stop and intent.recurrence_count is None:
            await core._finish(message.message_id, _question(language, "recurrence"), message.sender)
            return
        status, reminder = repository.update_recurrence(
            message.sender,
            recurrence_days=None if intent.recurrence_stop else intent.recurrence_days,
            recurrence_count=None if intent.recurrence_stop else intent.recurrence_count,
            weekdays_only=False if intent.recurrence_stop else intent.weekdays_only,
            position=intent.position,
        )
        if status == "ambiguous":
            await core._finish(
                message.message_id,
                base.reminder_recurrence_selection_message(
                    language, repository.list(message.sender, active_only=True, limit=10)
                ),
                message.sender,
            )
            return
        if status == "not_found":
            await core._finish(message.message_id, base.reminder_cancelled_message(language, 0), message.sender)
            return
        if status in {"conflict", "invalid"}:
            await core._finish(message.message_id, base.reminder_reschedule_conflict_message(language), message.sender)
            return
        await core._finish(
            message.message_id, base.reminder_recurrence_updated_message(language, reminder), message.sender
        )
        return
    if intent.action == "list":
        await core._finish(message.message_id, base.reminder_list_message(language, repository.list(message.sender, active_only=True, limit=10)), message.sender)
        return
    if intent.action == "cancel_all":
        count = repository.cancel(message.sender, all_active=True)
        await core._finish(message.message_id, base.reminder_cancelled_message(language, count), message.sender)
        return
    if intent.action == "cancel":
        active = repository.list(message.sender, active_only=True, limit=30)
        if intent.positions:
            count = repository.cancel_selected(message.sender, intent.positions)
        elif len(active) > 1:
            await core._finish(message.message_id, base.reminder_cancel_selection_message(language, active), message.sender)
            return
        else:
            count = repository.cancel(message.sender)
        await core._finish(message.message_id, base.reminder_cancelled_message(language, count), message.sender)
        return
    if intent.action == "reschedule":
        if not base.reminder_delivery_ready(message.sender):
            await core._finish(message.message_id, base.reminder_unavailable_message(language), message.sender)
            return
        if intent.scheduled_at is None or intent.scheduled_at.astimezone(UTC) <= datetime.now(UTC):
            await core._finish(message.message_id, _question(language, "time"), message.sender)
            return
        status, reminder = repository.reschedule(
            message.sender,
            scheduled_at=intent.scheduled_at,
            position=intent.position,
        )
        if status == "ambiguous":
            active = repository.list(message.sender, active_only=True, limit=10)
            await core._finish(message.message_id, base.reminder_selection_message(language, active), message.sender)
            return
        if status == "not_found":
            await core._finish(message.message_id, base.reminder_cancelled_message(language, 0), message.sender)
            return
        if status == "conflict":
            await core._finish(message.message_id, base.reminder_reschedule_conflict_message(language), message.sender)
            return
        await core._finish(message.message_id, base.reminder_rescheduled_message(language, reminder), message.sender)
        return
    if not base.reminder_delivery_ready(message.sender):
        _clear_pending(message.sender)
        await core._finish(message.message_id, base.reminder_unavailable_message(language), message.sender)
        return

    mission = core._hero_memory().get_latest_mission(message.sender)
    scheduled_at = resolve_conversational_schedule(intent, mission)
    title = intent.title or _real_mission_title(mission)

    if scheduled_at is None:
        pending = {
            _PENDING_TITLE: title,
            "session_language": language,
            "session_expires_at": core._session_expiry(),
        }
        if intent.recurrence_days is not None:
            pending[_PENDING_RECURRENCE_DAYS] = str(intent.recurrence_days)
            pending[_PENDING_RECURRENCE_COUNT] = str(intent.recurrence_count or "")
            pending[_PENDING_WEEKDAYS_ONLY] = "1" if intent.weekdays_only else "0"
        core.store.update_user(message.sender, pending)
        await core._finish(message.message_id, _question(language, "time"), message.sender)
        return
    if not title:
        pending = {
            _PENDING_AT: scheduled_at.isoformat(),
            "session_language": language,
            "session_expires_at": core._session_expiry(),
        }
        if intent.recurrence_days is not None:
            pending[_PENDING_RECURRENCE_DAYS] = str(intent.recurrence_days)
            pending[_PENDING_RECURRENCE_COUNT] = str(intent.recurrence_count or "")
            pending[_PENDING_WEEKDAYS_ONLY] = "1" if intent.weekdays_only else "0"
        core.store.update_user(message.sender, pending)
        await core._finish(message.message_id, _question(language, "title"), message.sender)
        return
    if intent.recurrence_days is not None and intent.recurrence_count is None:
        core.store.update_user(message.sender, {
            _PENDING_AT: scheduled_at.isoformat(),
            _PENDING_TITLE: title,
            _PENDING_RECURRENCE_DAYS: str(intent.recurrence_days),
            _PENDING_RECURRENCE_COUNT: "",
            _PENDING_WEEKDAYS_ONLY: "1" if intent.weekdays_only else "0",
            "session_language": language,
            "session_expires_at": core._session_expiry(),
        })
        await core._finish(message.message_id, _question(language, "recurrence"), message.sender)
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
        recurrence_days=intent.recurrence_days,
        recurrence_count=intent.recurrence_count,
        weekdays_only=intent.weekdays_only,
    )
    await core._finish(message.message_id, base.reminder_created_message(language, reminder), message.sender)


base.process_incoming = process_incoming
core.process_incoming = process_incoming
app = composed.app
store = composed.store
