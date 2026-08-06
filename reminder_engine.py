"""Consent-aware mission reminders for AmtHero24.

The module deliberately separates reminder intent parsing, durable persistence, and
outbound delivery. Recipients are encrypted at rest because a one-way phone hash is
not sufficient for a future WhatsApp send.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Awaitable, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken
from psycopg.types.json import Jsonb

from german_holidays import (
    canonical_german_state_code,
    german_state_label,
    is_german_statewide_public_holiday,
)

logger = logging.getLogger("amthero24.reminders")

SUPPORTED_LANGUAGES = {"de", "ar", "en", "uk", "el"}
DEFAULT_TIMEZONE = "Europe/Berlin"
SERVICE_WINDOW = timedelta(hours=24)


class ReminderServiceError(RuntimeError):
    """Stable reminder failure category."""


@dataclass(frozen=True)
class ReminderIntent:
    action: str
    scheduled_at: datetime | None = None
    lead_days: int | None = None


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").casefold().strip()
    value = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", value)
    value = re.sub(r"[؟،؛!?.,:;]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


_CREATE_MARKERS = (
    "ذكرني", "ذكّرني", "تذكرني", "نبهني", "نبّهني",
    "erinnere mich", "erinnerung", "remind me", "reminder",
    "нагадай мені", "нагадування", "θυμισε μου", "υπενθυμιση",
)
_LIST_MARKERS = (
    "شو تذكيراتي", "اعرض تذكيراتي", "التذكيرات", "meine erinnerungen",
    "erinnerungen anzeigen", "my reminders", "show reminders",
    "мої нагадування", "οι υπενθυμισεις μου",
)
_CANCEL_ALL_MARKERS = (
    "وقف كل التذكيرات", "الغ كل التذكيرات", "الغي كل التذكيرات",
    "alle erinnerungen stoppen", "cancel all reminders", "stop all reminders",
    "скасуй усі нагадування", "ακυρωσε ολες τις υπενθυμισεις",
)
_CANCEL_MARKERS = (
    "وقف التذكير", "الغ التذكير", "الغي التذكير", "احذف التذكير",
    "erinnerung stoppen", "erinnerung löschen", "cancel reminder",
    "delete reminder", "скасуй нагадування", "ακυρωσε την υπενθυμιση",
)

_ARABIC_NUMBERS = {
    "واحد": 1, "وحدة": 1, "يوم": 1, "يومين": 2, "اثنين": 2, "ثلاث": 3,
    "ثلاثة": 3, "اربع": 4, "أربع": 4, "اربعة": 4, "خمسة": 5,
    "ستة": 6, "سبعة": 7,
}


def _number(value: str) -> int | None:
    cleaned = _normalize(value)
    if cleaned.isdigit():
        return int(cleaned)
    return _ARABIC_NUMBERS.get(cleaned)


def _local_at(day: date, hour: int = 9, timezone_name: str = DEFAULT_TIMEZONE) -> datetime:
    return datetime.combine(day, time(hour=hour), tzinfo=ZoneInfo(timezone_name))


def _parse_absolute_date(text: str, timezone_name: str) -> datetime | None:
    iso_match = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", text or "")
    if iso_match:
        raw = f"{iso_match.group(1)}-{int(iso_match.group(2)):02d}-{int(iso_match.group(3)):02d}"
    else:
        local_match = re.search(r"\b(\d{1,2})[./](\d{1,2})[./](20\d{2})\b", text or "")
        if not local_match:
            return None
        raw = f"{local_match.group(3)}-{int(local_match.group(2)):02d}-{int(local_match.group(1)):02d}"
    try:
        return _local_at(datetime.strptime(raw, "%Y-%m-%d").date(), timezone_name=timezone_name)
    except ValueError:
        return None


def detect_reminder_intent(
    text: str,
    *,
    now: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> ReminderIntent | None:
    normalized = _normalize(text)
    if not normalized:
        return None
    if any(_normalize(marker) in normalized for marker in _CANCEL_ALL_MARKERS):
        return ReminderIntent("cancel_all")
    if any(_normalize(marker) in normalized for marker in _CANCEL_MARKERS):
        return ReminderIntent("cancel")
    if any(_normalize(marker) in normalized for marker in _LIST_MARKERS):
        return ReminderIntent("list")
    if not any(_normalize(marker) in normalized for marker in _CREATE_MARKERS):
        return None

    current = (now or datetime.now(UTC)).astimezone(ZoneInfo(timezone_name))
    absolute = _parse_absolute_date(text, timezone_name)
    if absolute:
        return ReminderIntent("create", scheduled_at=absolute)

    if any(token in normalized for token in ("بكرا", "غدا", "غداً", "morgen", "tomorrow", "завтра", "αυριο")):
        return ReminderIntent("create", scheduled_at=_local_at(current.date() + timedelta(days=1), timezone_name=timezone_name))

    hour_match = re.search(
        r"(?:بعد|in|через|σε)\s+(\d{1,3})\s*(?:ساعة|ساعات|stunden?|hours?|годин|ωρες)",
        normalized,
    )
    if hour_match:
        return ReminderIntent("create", scheduled_at=current + timedelta(hours=int(hour_match.group(1))))

    day_match = re.search(
        r"(?:بعد|in|через|σε)\s+(\d{1,3})\s*(?:يوم|ايام|أيام|tagen?|days?|дн(?:і|ів)|ημερες)",
        normalized,
    )
    if day_match:
        return ReminderIntent(
            "create",
            scheduled_at=_local_at(current.date() + timedelta(days=int(day_match.group(1))), timezone_name=timezone_name),
        )

    lead_match = re.search(
        r"(?:قبلها|قبل الموعد|قبل المهلة|vorher|before|до|πριν)\s*(?:ب|um|by)?\s*([\w\u0600-\u06ff]+)\s*(?:يوم|ايام|أيام|tagen?|days?|дн(?:і|ів)|ημερες)",
        normalized,
    )
    if lead_match:
        lead_days = _number(lead_match.group(1))
        if lead_days is not None:
            return ReminderIntent("create", lead_days=max(0, min(lead_days, 365)))

    return ReminderIntent("create")


def resolve_reminder_schedule(
    intent: ReminderIntent,
    mission: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> datetime | None:
    current = (now or datetime.now(UTC)).astimezone(ZoneInfo(timezone_name))
    scheduled = intent.scheduled_at
    if scheduled is None and mission and mission.get("due_at"):
        try:
            due = date.fromisoformat(str(mission["due_at"]))
        except ValueError:
            due = None
        if due:
            lead = intent.lead_days if intent.lead_days is not None else 1
            scheduled = _local_at(due - timedelta(days=lead), timezone_name=timezone_name)
    if scheduled is None:
        return None
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=ZoneInfo(timezone_name))
    local = scheduled.astimezone(ZoneInfo(timezone_name))
    if local.hour >= 21:
        local = _local_at(local.date() + timedelta(days=1), timezone_name=timezone_name)
    elif local.hour < 8:
        local = _local_at(local.date(), timezone_name=timezone_name)
    if local <= current:
        return None
    return local.astimezone(UTC)


def _phone_hash(phone: str) -> str:
    normalized = "".join(character for character in phone if character.isdigit() or character == "+")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def reminder_recipient_hash(phone: str) -> str:
    """Return the same one-way recipient key used by reminder persistence."""
    return _phone_hash(phone)


def _fernet() -> Fernet:
    secret = os.getenv("REMINDER_ENCRYPTION_KEY", "").strip() or os.getenv("WHATSAPP_TOKEN", "").strip()
    if not secret:
        raise ReminderServiceError("missing_encryption_key")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_recipient(phone: str) -> str:
    if not phone:
        raise ReminderServiceError("missing_recipient")
    return _fernet().encrypt(phone.encode("utf-8")).decode("ascii")


def decrypt_recipient(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise ReminderServiceError("recipient_decryption_failed") from exc


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _parse_recurrence_weekdays(value: Any) -> tuple[int, ...] | None:
    """Parse the compact weekday representation, returning None for corrupt data."""
    if value is None or value == "":
        return ()
    raw_values = value.split(",") if isinstance(value, str) else value
    if not isinstance(raw_values, (list, tuple, set, frozenset)):
        return None
    weekdays: set[int] = set()
    for raw in raw_values:
        if isinstance(raw, bool):
            return None
        text = str(raw).strip()
        if not text.isdigit():
            return None
        weekday = int(text)
        if not 0 <= weekday <= 6:
            return None
        weekdays.add(weekday)
    return tuple(sorted(weekdays)) if weekdays else None


def _canonical_recurrence_weekdays(value: Any) -> str:
    weekdays = _parse_recurrence_weekdays(value)
    if not weekdays:
        raise ReminderServiceError("invalid_recurrence")
    return ",".join(str(weekday) for weekday in weekdays)


def _canonical_holiday_region(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    region = canonical_german_state_code(raw)
    if not region:
        raise ReminderServiceError("invalid_holiday_region")
    return region


def _occurrence_at_or_after(
    scheduled_at: datetime,
    timezone_name: str,
    allowed_weekdays: tuple[int, ...],
    holiday_region: str = "",
) -> datetime:
    """Keep local clock time while finding the nearest valid schedule date."""
    try:
        timezone = ZoneInfo(timezone_name)
    except (KeyError, ValueError):
        timezone = ZoneInfo(DEFAULT_TIMEZONE)
    local = scheduled_at.astimezone(timezone)
    region = _canonical_holiday_region(holiday_region)
    valid_weekdays = allowed_weekdays or (tuple(range(7)) if region else ())
    if not valid_weekdays:
        return scheduled_at.astimezone(UTC)
    for offset in range(371):
        candidate = local.date() + timedelta(days=offset)
        if candidate.weekday() not in valid_weekdays:
            continue
        if region and is_german_statewide_public_holiday(candidate, region):
            continue
        return datetime.combine(candidate, local.timetz()).astimezone(UTC)
    raise ReminderServiceError("invalid_schedule")


def _weekday_occurrence_at_or_after(scheduled_at: datetime, timezone_name: str) -> datetime:
    """Keep the local clock while moving weekend occurrences to Monday."""
    return _occurrence_at_or_after(scheduled_at, timezone_name, (0, 1, 2, 3, 4))


def _allowed_recurrence_weekdays(item: dict[str, Any]) -> tuple[int, ...] | None:
    specific = _parse_recurrence_weekdays(item.get("recurrence_weekdays"))
    if specific is None:
        return None
    if specific:
        return specific
    return (0, 1, 2, 3, 4) if item.get("weekdays_only") else ()


def _stored_holiday_region(item: dict[str, Any]) -> str | None:
    raw = str(item.get("holiday_region") or "").strip()
    if not raw:
        return ""
    return canonical_german_state_code(raw) or None


def _weekly_anchor_weekday(item: dict[str, Any], scheduled_at: datetime) -> tuple[int, ...]:
    if int(item.get("recurrence_days") or 0) != 7:
        return ()
    timezone_name = str(item.get("timezone") or DEFAULT_TIMEZONE)
    try:
        timezone = ZoneInfo(timezone_name)
    except (KeyError, ValueError):
        timezone = ZoneInfo(DEFAULT_TIMEZONE)
    return (scheduled_at.astimezone(timezone).weekday(),)


class ReminderRepository:
    """Durable reminders over PostgreSQL, with a local JSON fallback for tests."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.backend_name = str(getattr(store, "backend_name", "json"))
        if self.backend_name == "postgresql":
            self._initialize_postgres_schema()

    def _initialize_postgres_schema(self) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS hero_reminders (
                reminder_id TEXT PRIMARY KEY,
                dedupe_key TEXT UNIQUE NOT NULL,
                phone_hash TEXT NOT NULL,
                recipient_ciphertext TEXT NOT NULL,
                mission_id TEXT,
                title TEXT NOT NULL,
                language TEXT NOT NULL DEFAULT 'de',
                timezone TEXT NOT NULL DEFAULT 'Europe/Berlin',
                scheduled_at TIMESTAMPTZ NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                next_attempt_at TIMESTAMPTZ NOT NULL,
                lease_until TIMESTAMPTZ,
                sent_at TIMESTAMPTZ,
                recurrence_days INTEGER,
                recurrence_remaining INTEGER,
                weekdays_only BOOLEAN NOT NULL DEFAULT FALSE,
                recurrence_weekdays TEXT NOT NULL DEFAULT '',
                holiday_region TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            "ALTER TABLE hero_reminders ADD COLUMN IF NOT EXISTS recurrence_days INTEGER",
            "ALTER TABLE hero_reminders ADD COLUMN IF NOT EXISTS recurrence_remaining INTEGER",
            "ALTER TABLE hero_reminders ADD COLUMN IF NOT EXISTS weekdays_only BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE hero_reminders ADD COLUMN IF NOT EXISTS recurrence_weekdays TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE hero_reminders ADD COLUMN IF NOT EXISTS holiday_region TEXT NOT NULL DEFAULT ''",
            """
            CREATE INDEX IF NOT EXISTS hero_reminders_due_idx
            ON hero_reminders (status, next_attempt_at, scheduled_at)
            """,
            """
            CREATE INDEX IF NOT EXISTS hero_reminders_phone_idx
            ON hero_reminders (phone_hash, status, scheduled_at)
            """,
        )
        with self.store.pool.connection() as connection:
            for statement in statements:
                connection.execute(statement)

    @staticmethod
    def _dedupe(
        phone_hash: str,
        mission_id: str,
        scheduled_at: datetime,
        title: str,
        recurrence_days: int | None = None,
        holiday_region: str = "",
    ) -> str:
        raw = (
            f"{phone_hash}|{mission_id}|{scheduled_at.astimezone(UTC).isoformat()}|"
            f"{title.casefold()}|{recurrence_days or 0}"
        )
        if holiday_region:
            raw += f"|holiday:{holiday_region}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def create(
        self,
        phone: str,
        *,
        title: str,
        scheduled_at: datetime,
        language: str,
        mission_id: str = "",
        timezone_name: str = DEFAULT_TIMEZONE,
        recurrence_days: int | None = None,
        recurrence_count: int | None = None,
        weekdays_only: bool = False,
        recurrence_weekdays: tuple[int, ...] | list[int] | set[int] | None = None,
        holiday_region: str = "",
    ) -> dict[str, Any]:
        when = scheduled_at if scheduled_at.tzinfo else scheduled_at.replace(tzinfo=UTC)
        when = when.astimezone(UTC)
        phone_hash = _phone_hash(phone)
        clean_title = _clean(title, 180) or "Follow-up"
        clean_mission = _clean(mission_id, 64)
        lang = language if language in SUPPORTED_LANGUAGES else "de"
        repeat_days = int(recurrence_days) if recurrence_days is not None else None
        repeat_count = int(recurrence_count) if recurrence_count is not None else None
        weekday_schedule = bool(weekdays_only)
        specific_weekdays = (
            _canonical_recurrence_weekdays(recurrence_weekdays)
            if recurrence_weekdays is not None else ""
        )
        region = _canonical_holiday_region(holiday_region)
        if (repeat_days is None) != (repeat_count is None):
            raise ReminderServiceError("invalid_recurrence")
        if repeat_days is not None and (not 1 <= repeat_days <= 365 or not 2 <= repeat_count <= 365):
            raise ReminderServiceError("invalid_recurrence")
        if weekday_schedule and repeat_days != 1:
            raise ReminderServiceError("invalid_recurrence")
        if specific_weekdays and (repeat_days != 1 or weekday_schedule):
            raise ReminderServiceError("invalid_recurrence")
        allowed_weekdays = (
            _parse_recurrence_weekdays(specific_weekdays)
            if specific_weekdays else ((0, 1, 2, 3, 4) if weekday_schedule else ())
        )
        if region and repeat_days == 7 and not allowed_weekdays:
            try:
                timezone = ZoneInfo(timezone_name)
            except (KeyError, ValueError):
                timezone = ZoneInfo(DEFAULT_TIMEZONE)
            allowed_weekdays = (when.astimezone(timezone).weekday(),)
        if allowed_weekdays or region:
            when = _occurrence_at_or_after(when, timezone_name, allowed_weekdays, region)
        reminder = {
            "reminder_id": uuid4().hex,
            "dedupe_key": self._dedupe(
                phone_hash, clean_mission, when, clean_title, repeat_days, region,
            ),
            "phone_hash": phone_hash,
            "recipient_ciphertext": encrypt_recipient(phone),
            "mission_id": clean_mission,
            "title": clean_title,
            "language": lang,
            "timezone": timezone_name if timezone_name else DEFAULT_TIMEZONE,
            "scheduled_at": when.isoformat(),
            "status": "pending",
            "attempt_count": 0,
            "last_error": "",
            "next_attempt_at": when.isoformat(),
            "lease_until": None,
            "sent_at": None,
            "recurrence_days": repeat_days,
            "recurrence_remaining": repeat_count,
            "weekdays_only": weekday_schedule,
            "recurrence_weekdays": specific_weekdays,
            "holiday_region": region,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                row = connection.execute(
                    """
                    INSERT INTO hero_reminders
                        (reminder_id, dedupe_key, phone_hash, recipient_ciphertext, mission_id,
                        title, language, timezone, scheduled_at, next_attempt_at,
                        recurrence_days, recurrence_remaining, weekdays_only, recurrence_weekdays,
                        holiday_region)
                    VALUES (%s, %s, %s, %s, NULLIF(%s, ''), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (dedupe_key) DO UPDATE
                    SET updated_at = NOW()
                    RETURNING *
                    """,
                    (
                        reminder["reminder_id"], reminder["dedupe_key"], phone_hash,
                        reminder["recipient_ciphertext"], clean_mission, clean_title, lang,
                        reminder["timezone"], when, when, repeat_days, repeat_count,
                        weekday_schedule, specific_weekdays, region,
                    ),
                ).fetchone()
            return self._from_row(row)

        def add(data: dict[str, Any]) -> dict[str, Any]:
            reminders = data.setdefault("reminders", {})
            for existing in reminders.values():
                if existing.get("dedupe_key") == reminder["dedupe_key"]:
                    return deepcopy(existing)
            reminders[reminder["reminder_id"]] = deepcopy(reminder)
            return deepcopy(reminder)

        return self.store._transaction(add)

    def list(self, phone: str, *, active_only: bool = True, limit: int = 10) -> list[dict[str, Any]]:
        key = _phone_hash(phone)
        safe_limit = max(1, min(int(limit), 30))
        active_statuses = ("pending", "failed", "blocked_template", "processing")
        if self.backend_name == "postgresql":
            condition = "phone_hash = %s"
            params: list[Any] = [key]
            if active_only:
                condition += " AND status = ANY(%s)"
                params.append(list(active_statuses))
            with self.store.pool.connection() as connection:
                rows = connection.execute(
                    f"SELECT * FROM hero_reminders WHERE {condition} ORDER BY scheduled_at ASC LIMIT %s",
                    (*params, safe_limit),
                ).fetchall()
            return [self._from_row(row) for row in rows]
        records = [
            deepcopy(item)
            for item in self.store.snapshot().get("reminders", {}).values()
            if isinstance(item, dict)
            and item.get("phone_hash") == key
            and (not active_only or item.get("status") in active_statuses)
        ]
        records.sort(key=lambda item: str(item.get("scheduled_at") or ""))
        return records[:safe_limit]

    def cancel(self, phone: str, *, all_active: bool = False) -> int:
        key = _phone_hash(phone)
        statuses = ("pending", "failed", "blocked_template", "processing")
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                if all_active:
                    cursor = connection.execute(
                        """
                        UPDATE hero_reminders SET status = 'cancelled', updated_at = NOW(), lease_until = NULL
                        WHERE phone_hash = %s AND status = ANY(%s)
                        """,
                        (key, list(statuses)),
                    )
                else:
                    cursor = connection.execute(
                        """
                        WITH latest AS (
                            SELECT reminder_id FROM hero_reminders
                            WHERE phone_hash = %s AND status = ANY(%s)
                            ORDER BY scheduled_at ASC LIMIT 1 FOR UPDATE
                        )
                        UPDATE hero_reminders AS reminder
                        SET status = 'cancelled', updated_at = NOW(), lease_until = NULL
                        FROM latest WHERE reminder.reminder_id = latest.reminder_id
                        """,
                        (key, list(statuses)),
                    )
            return max(cursor.rowcount, 0)

        def cancel_json(data: dict[str, Any]) -> int:
            candidates = [
                item for item in data.setdefault("reminders", {}).values()
                if isinstance(item, dict) and item.get("phone_hash") == key and item.get("status") in statuses
            ]
            candidates.sort(key=lambda item: str(item.get("scheduled_at") or ""))
            selected = candidates if all_active else candidates[:1]
            for item in selected:
                item["status"] = "cancelled"
                item["updated_at"] = datetime.now(UTC).isoformat()
                item["lease_until"] = None
            return len(selected)

        return self.store._transaction(cancel_json)

    def cancel_selected(self, phone: str, positions: tuple[int, ...]) -> int:
        """Cancel numbered reminders atomically using the displayed list order."""
        selected_positions = tuple(sorted(set(int(value) for value in positions)))
        if not selected_positions or any(value < 1 or value > 30 for value in selected_positions):
            return 0
        key = _phone_hash(phone)
        statuses = ("pending", "failed", "blocked_template", "processing")
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                rows = connection.execute(
                    """
                    SELECT reminder_id FROM hero_reminders
                    WHERE phone_hash = %s AND status = ANY(%s)
                    ORDER BY scheduled_at ASC LIMIT 30 FOR UPDATE
                    """,
                    (key, list(statuses)),
                ).fetchall()
                if not rows or selected_positions[-1] > len(rows):
                    return 0
                reminder_ids = [str(rows[position - 1]["reminder_id"]) for position in selected_positions]
                cursor = connection.execute(
                    """
                    UPDATE hero_reminders
                    SET status = 'cancelled', updated_at = NOW(), lease_until = NULL
                    WHERE reminder_id = ANY(%s)
                    """,
                    (reminder_ids,),
                )
            return max(cursor.rowcount, 0)

        def cancel_json(data: dict[str, Any]) -> int:
            candidates = [
                item for item in data.setdefault("reminders", {}).values()
                if isinstance(item, dict) and item.get("phone_hash") == key and item.get("status") in statuses
            ]
            candidates.sort(key=lambda item: str(item.get("scheduled_at") or ""))
            if not candidates or selected_positions[-1] > len(candidates):
                return 0
            current = datetime.now(UTC).isoformat()
            for position in selected_positions:
                item = candidates[position - 1]
                item["status"] = "cancelled"
                item["updated_at"] = current
                item["lease_until"] = None
            return len(selected_positions)

        return self.store._transaction(cancel_json)

    def reschedule(
        self,
        phone: str,
        *,
        scheduled_at: datetime,
        position: int | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Move one active reminder without guessing when several are present."""
        key = _phone_hash(phone)
        when = scheduled_at if scheduled_at.tzinfo else scheduled_at.replace(tzinfo=UTC)
        when = when.astimezone(UTC)
        statuses = ("pending", "failed", "blocked_template")
        if position is not None and not 1 <= position <= 30:
            return "not_found", {}

        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM hero_reminders
                    WHERE phone_hash = %s AND status = ANY(%s)
                    ORDER BY scheduled_at ASC LIMIT 30 FOR UPDATE
                    """,
                    (key, list(statuses)),
                ).fetchall()
                if not rows:
                    return "not_found", {}
                if position is None and len(rows) != 1:
                    return "ambiguous", {}
                index = (position - 1) if position is not None else 0
                if index >= len(rows):
                    return "not_found", {}
                selected = dict(rows[index])
                allowed_weekdays = _allowed_recurrence_weekdays(selected)
                region = _stored_holiday_region(selected)
                selected_schedule = _parse_datetime(selected.get("scheduled_at"))
                if allowed_weekdays is None or region is None or selected_schedule is None:
                    return "conflict", {}
                if region and not allowed_weekdays:
                    allowed_weekdays = _weekly_anchor_weekday(selected, selected_schedule)
                target_when = _occurrence_at_or_after(
                    when,
                    str(selected.get("timezone") or DEFAULT_TIMEZONE),
                    allowed_weekdays,
                    region,
                ) if allowed_weekdays or region else when
                dedupe = self._dedupe(
                    key,
                    str(selected.get("mission_id") or ""),
                    target_when,
                    str(selected.get("title") or "Follow-up"),
                    int(selected["recurrence_days"]) if selected.get("recurrence_days") else None,
                    region,
                )
                conflict = connection.execute(
                    "SELECT 1 FROM hero_reminders WHERE dedupe_key = %s AND reminder_id <> %s LIMIT 1",
                    (dedupe, selected["reminder_id"]),
                ).fetchone()
                if conflict:
                    return "conflict", {}
                row = connection.execute(
                    """
                    UPDATE hero_reminders
                    SET scheduled_at = %s, next_attempt_at = %s, dedupe_key = %s,
                        status = 'pending', attempt_count = 0, last_error = '',
                        lease_until = NULL, sent_at = NULL, updated_at = NOW()
                    WHERE reminder_id = %s
                    RETURNING *
                    """,
                    (target_when, target_when, dedupe, selected["reminder_id"]),
                ).fetchone()
            return "updated", self._from_row(row)

        def update_json(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            candidates = [
                item for item in data.setdefault("reminders", {}).values()
                if isinstance(item, dict) and item.get("phone_hash") == key and item.get("status") in statuses
            ]
            candidates.sort(key=lambda item: str(item.get("scheduled_at") or ""))
            if not candidates:
                return "not_found", {}
            if position is None and len(candidates) != 1:
                return "ambiguous", {}
            index = (position - 1) if position is not None else 0
            if index >= len(candidates):
                return "not_found", {}
            item = candidates[index]
            allowed_weekdays = _allowed_recurrence_weekdays(item)
            region = _stored_holiday_region(item)
            selected_schedule = _parse_datetime(item.get("scheduled_at"))
            if allowed_weekdays is None or region is None or selected_schedule is None:
                return "conflict", {}
            if region and not allowed_weekdays:
                allowed_weekdays = _weekly_anchor_weekday(item, selected_schedule)
            target_when = _occurrence_at_or_after(
                when,
                str(item.get("timezone") or DEFAULT_TIMEZONE),
                allowed_weekdays,
                region,
            ) if allowed_weekdays or region else when
            dedupe = self._dedupe(
                key,
                str(item.get("mission_id") or ""),
                target_when,
                str(item.get("title") or "Follow-up"),
                int(item["recurrence_days"]) if item.get("recurrence_days") else None,
                region,
            )
            if any(
                existing is not item and isinstance(existing, dict) and existing.get("dedupe_key") == dedupe
                for existing in data.setdefault("reminders", {}).values()
            ):
                return "conflict", {}
            item.update({
                "scheduled_at": target_when.isoformat(),
                "next_attempt_at": target_when.isoformat(),
                "dedupe_key": dedupe,
                "status": "pending",
                "attempt_count": 0,
                "last_error": "",
                "lease_until": None,
                "sent_at": None,
                "updated_at": datetime.now(UTC).isoformat(),
            })
            return "updated", deepcopy(item)

        return self.store._transaction(update_json)

    def update_recurrence(
        self,
        phone: str,
        *,
        recurrence_days: int | None,
        recurrence_count: int | None,
        weekdays_only: bool = False,
        recurrence_weekdays: tuple[int, ...] | list[int] | set[int] | None = None,
        holiday_region: str | None = None,
        position: int | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Change or stop recurrence without guessing between active reminders."""
        if position is not None and not 1 <= position <= 30:
            return "not_found", {}
        if (recurrence_days is None) != (recurrence_count is None):
            return "invalid", {}
        if recurrence_days is not None and (
            recurrence_days not in {1, 7} or not 2 <= recurrence_count <= 365
        ):
            return "invalid", {}
        weekday_schedule = bool(weekdays_only)
        try:
            specific_weekdays = (
                _canonical_recurrence_weekdays(recurrence_weekdays)
                if recurrence_weekdays is not None else ""
            )
            region_override = (
                None if holiday_region is None else _canonical_holiday_region(holiday_region)
            )
        except ReminderServiceError:
            return "invalid", {}
        if weekday_schedule and recurrence_days != 1:
            return "invalid", {}
        if specific_weekdays and (recurrence_days != 1 or weekday_schedule):
            return "invalid", {}
        if recurrence_days is None and (weekday_schedule or specific_weekdays or region_override):
            return "invalid", {}
        key = _phone_hash(phone)
        statuses = ("pending", "failed", "blocked_template")

        def recurrence_values(item: dict[str, Any]) -> tuple[str, datetime, str]:
            scheduled = _parse_datetime(item.get("scheduled_at"))
            if scheduled is None:
                raise ReminderServiceError("invalid_schedule")
            stored_region = _stored_holiday_region(item)
            if stored_region is None:
                raise ReminderServiceError("invalid_holiday_region")
            region = stored_region if region_override is None else region_override
            if recurrence_days is None:
                region = ""
            allowed_weekdays = (
                _parse_recurrence_weekdays(specific_weekdays)
                if specific_weekdays else ((0, 1, 2, 3, 4) if weekday_schedule else ())
            )
            if region and recurrence_days == 7 and not allowed_weekdays:
                allowed_weekdays = _weekly_anchor_weekday(
                    {**item, "recurrence_days": recurrence_days}, scheduled,
                )
            if allowed_weekdays or region:
                scheduled = _occurrence_at_or_after(
                    scheduled,
                    str(item.get("timezone") or DEFAULT_TIMEZONE),
                    allowed_weekdays,
                    region,
                )
            dedupe = self._dedupe(
                key,
                str(item.get("mission_id") or ""),
                scheduled,
                str(item.get("title") or "Follow-up"),
                recurrence_days,
                region,
            )
            return dedupe, scheduled, region

        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM hero_reminders
                    WHERE phone_hash = %s AND status = ANY(%s)
                    ORDER BY scheduled_at ASC LIMIT 30 FOR UPDATE
                    """,
                    (key, list(statuses)),
                ).fetchall()
                if not rows:
                    return "not_found", {}
                if position is None and len(rows) != 1:
                    return "ambiguous", {}
                index = (position - 1) if position is not None else 0
                if index >= len(rows):
                    return "not_found", {}
                selected = dict(rows[index])
                dedupe, scheduled, region = recurrence_values(selected)
                conflict = connection.execute(
                    "SELECT 1 FROM hero_reminders WHERE dedupe_key = %s AND reminder_id <> %s LIMIT 1",
                    (dedupe, selected["reminder_id"]),
                ).fetchone()
                if conflict:
                    return "conflict", {}
                row = connection.execute(
                    """
                    UPDATE hero_reminders
                    SET recurrence_days = %s, recurrence_remaining = %s, weekdays_only = %s,
                        recurrence_weekdays = %s, holiday_region = %s,
                        scheduled_at = %s, dedupe_key = %s,
                        status = 'pending', attempt_count = 0, last_error = '',
                        next_attempt_at = %s, lease_until = NULL, updated_at = NOW()
                    WHERE reminder_id = %s RETURNING *
                    """,
                    (
                        recurrence_days, recurrence_count, weekday_schedule, specific_weekdays, region,
                        scheduled, dedupe, scheduled, selected["reminder_id"],
                    ),
                ).fetchone()
            return "updated", self._from_row(row)

        def update_json(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            candidates = [
                item for item in data.setdefault("reminders", {}).values()
                if isinstance(item, dict) and item.get("phone_hash") == key and item.get("status") in statuses
            ]
            candidates.sort(key=lambda item: str(item.get("scheduled_at") or ""))
            if not candidates:
                return "not_found", {}
            if position is None and len(candidates) != 1:
                return "ambiguous", {}
            index = (position - 1) if position is not None else 0
            if index >= len(candidates):
                return "not_found", {}
            item = candidates[index]
            dedupe, scheduled, region = recurrence_values(item)
            if any(
                existing is not item and isinstance(existing, dict) and existing.get("dedupe_key") == dedupe
                for existing in data.setdefault("reminders", {}).values()
            ):
                return "conflict", {}
            item.update({
                "recurrence_days": recurrence_days,
                "recurrence_remaining": recurrence_count,
                "weekdays_only": weekday_schedule,
                "recurrence_weekdays": specific_weekdays,
                "holiday_region": region,
                "scheduled_at": scheduled.isoformat(),
                "dedupe_key": dedupe,
                "status": "pending",
                "attempt_count": 0,
                "last_error": "",
                "next_attempt_at": scheduled.isoformat(),
                "lease_until": None,
                "updated_at": datetime.now(UTC).isoformat(),
            })
            return "updated", deepcopy(item)

        return self.store._transaction(update_json)

    def claim_due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 10,
        allowed_phone_hashes: set[str] | frozenset[str] | None = None,
    ) -> list[dict[str, Any]]:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        safe_limit = max(1, min(int(limit), 50))
        retry_statuses = ("pending", "failed", "blocked_template")
        allowed = None if allowed_phone_hashes is None else sorted(allowed_phone_hashes)
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                rows = connection.execute(
                    """
                    WITH due AS (
                        SELECT reminder_id FROM hero_reminders
                        WHERE status = ANY(%s)
                          AND scheduled_at <= %s
                          AND next_attempt_at <= %s
                          AND (lease_until IS NULL OR lease_until < %s)
                          AND (%s::text[] IS NULL OR phone_hash = ANY(%s))
                        ORDER BY scheduled_at ASC
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE hero_reminders AS reminder
                    SET status = 'processing', lease_until = %s, attempt_count = attempt_count + 1,
                        updated_at = NOW()
                    FROM due WHERE reminder.reminder_id = due.reminder_id
                    RETURNING reminder.*
                    """,
                    (
                        list(retry_statuses), current, current, current,
                        allowed, allowed, safe_limit, current + timedelta(minutes=5),
                    ),
                ).fetchall()
            return [self._from_row(row) for row in rows]

        def claim_json(data: dict[str, Any]) -> list[dict[str, Any]]:
            eligible = []
            for item in data.setdefault("reminders", {}).values():
                if not isinstance(item, dict) or item.get("status") not in retry_statuses:
                    continue
                if allowed_phone_hashes is not None and item.get("phone_hash") not in allowed_phone_hashes:
                    continue
                try:
                    scheduled = datetime.fromisoformat(str(item.get("scheduled_at"))).astimezone(UTC)
                    next_attempt = datetime.fromisoformat(str(item.get("next_attempt_at"))).astimezone(UTC)
                    lease = datetime.fromisoformat(str(item.get("lease_until"))).astimezone(UTC) if item.get("lease_until") else None
                except (TypeError, ValueError):
                    continue
                if scheduled <= current and next_attempt <= current and (lease is None or lease < current):
                    eligible.append(item)
            eligible.sort(key=lambda item: str(item.get("scheduled_at")))
            selected = eligible[:safe_limit]
            for item in selected:
                item["status"] = "processing"
                item["lease_until"] = (current + timedelta(minutes=5)).isoformat()
                item["attempt_count"] = int(item.get("attempt_count") or 0) + 1
                item["updated_at"] = current.isoformat()
            return deepcopy(selected)

        return self.store._transaction(claim_json)

    def mark_sent(self, reminder_id: str, *, now: datetime | None = None) -> None:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        if self._advance_recurrence(reminder_id, current):
            return
        self._set_delivery_state(reminder_id, "sent", current, "", None, sent_at=current)

    @staticmethod
    def _next_occurrence(item: dict[str, Any]) -> datetime | None:
        days = int(item.get("recurrence_days") or 0)
        remaining = int(item.get("recurrence_remaining") or 0)
        scheduled = _parse_datetime(item.get("scheduled_at"))
        if days < 1 or remaining <= 1 or scheduled is None:
            return None
        timezone_name = str(item.get("timezone") or DEFAULT_TIMEZONE)
        try:
            local = scheduled.astimezone(ZoneInfo(timezone_name))
        except (KeyError, ValueError):
            local = scheduled.astimezone(ZoneInfo(DEFAULT_TIMEZONE))
        next_date = local.date() + timedelta(days=days)
        allowed_weekdays = _allowed_recurrence_weekdays(item)
        region = _stored_holiday_region(item)
        if allowed_weekdays is None or region is None:
            return None
        if region and not allowed_weekdays and days == 7:
            allowed_weekdays = (local.weekday(),)
        candidate = datetime.combine(next_date, local.timetz()).astimezone(UTC)
        if allowed_weekdays or region:
            return _occurrence_at_or_after(
                candidate, timezone_name, allowed_weekdays, region,
            )
        return candidate

    def _advance_recurrence(self, reminder_id: str, current: datetime) -> bool:
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                row = connection.execute(
                    "SELECT * FROM hero_reminders WHERE reminder_id = %s FOR UPDATE",
                    (reminder_id,),
                ).fetchone()
                item = dict(row) if row else {}
                next_at = self._next_occurrence(item)
                if next_at is None:
                    return False
                connection.execute(
                    """
                    UPDATE hero_reminders
                    SET scheduled_at = %s, next_attempt_at = %s,
                        recurrence_remaining = recurrence_remaining - 1,
                        status = 'pending', attempt_count = 0, last_error = '',
                        lease_until = NULL, sent_at = %s, updated_at = NOW()
                    WHERE reminder_id = %s
                    """,
                    (next_at, next_at, current, reminder_id),
                )
            return True

        def advance_json(data: dict[str, Any]) -> bool:
            item = data.setdefault("reminders", {}).get(reminder_id)
            if not isinstance(item, dict):
                return False
            next_at = self._next_occurrence(item)
            if next_at is None:
                return False
            item.update({
                "scheduled_at": next_at.isoformat(),
                "next_attempt_at": next_at.isoformat(),
                "recurrence_remaining": int(item.get("recurrence_remaining") or 0) - 1,
                "status": "pending",
                "attempt_count": 0,
                "last_error": "",
                "lease_until": None,
                "sent_at": current.isoformat(),
                "updated_at": current.isoformat(),
            })
            return True

        return self.store._transaction(advance_json)

    def mark_blocked(self, reminder_id: str, *, reason: str = "template_required", now: datetime | None = None) -> None:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        self._set_delivery_state(
            reminder_id, "blocked_template", current, reason, current + timedelta(hours=24), sent_at=None
        )

    def mark_failed(self, reminder_id: str, *, error: str, attempt_count: int, now: datetime | None = None) -> None:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        delay_minutes = min(24 * 60, 5 * (2 ** max(0, min(attempt_count - 1, 8))))
        status = "cancelled" if attempt_count >= 8 else "failed"
        next_attempt = None if status == "cancelled" else current + timedelta(minutes=delay_minutes)
        self._set_delivery_state(reminder_id, status, current, _clean(error, 180), next_attempt, sent_at=None)

    def _set_delivery_state(
        self,
        reminder_id: str,
        status: str,
        current: datetime,
        error: str,
        next_attempt: datetime | None,
        *,
        sent_at: datetime | None,
    ) -> None:
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                connection.execute(
                    """
                    UPDATE hero_reminders
                    SET status = %s, last_error = %s, next_attempt_at = COALESCE(%s, next_attempt_at),
                        lease_until = NULL, sent_at = %s, updated_at = NOW()
                    WHERE reminder_id = %s
                    """,
                    (status, error, next_attempt, sent_at, reminder_id),
                )
            return

        def update(data: dict[str, Any]) -> None:
            item = data.setdefault("reminders", {}).get(reminder_id)
            if not item:
                return
            item["status"] = status
            item["last_error"] = error
            if next_attempt is not None:
                item["next_attempt_at"] = next_attempt.isoformat()
            item["lease_until"] = None
            item["sent_at"] = sent_at.isoformat() if sent_at else None
            item["updated_at"] = current.isoformat()

        self.store._transaction(update)

    @staticmethod
    def _from_row(row: Any) -> dict[str, Any]:
        if not row:
            return {}
        result = dict(row)
        for field in ("scheduled_at", "next_attempt_at", "lease_until", "sent_at", "created_at", "updated_at"):
            value = result.get(field)
            if isinstance(value, datetime):
                result[field] = value.astimezone(UTC).isoformat()
        return result


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def service_window_open(profile: dict[str, Any], *, now: datetime | None = None) -> bool:
    last_seen = _parse_datetime(profile.get("last_seen"))
    current = (now or datetime.now(UTC)).astimezone(UTC)
    return bool(last_seen and timedelta(0) <= current - last_seen < SERVICE_WINDOW)


def render_reminder(language: str, title: str) -> str:
    lang = language if language in SUPPORTED_LANGUAGES else "de"
    clean_title = _clean(title, 180)
    return {
        "ar": f"تذكير لطيف 📅 عندك متابعة بخصوص «{clean_title}». افتح المحادثة واكتبلي شو صار، ومنكمل من آخر خطوة.",
        "de": f"Kleine Erinnerung 📅 Du hast eine offene متابعة zu „{clean_title}“. Schreib kurz, was passiert ist, dann machen wir beim letzten Schritt weiter.",
        "en": f"A quick reminder 📅 You have a follow-up for “{clean_title}”. Tell me what happened and we will continue from the last step.",
        "uk": f"Нагадування 📅 У тебе є подальша дія щодо «{clean_title}». Напиши, що сталося, і продовжимо з останнього кроку.",
        "el": f"Μικρή υπενθύμιση 📅 Έχεις συνέχεια για «{clean_title}». Γράψε μου τι έγινε και συνεχίζουμε από το τελευταίο βήμα.",
    }[lang]


def template_language(language: str) -> str:
    return {"de": "de", "ar": "ar", "en": "en_US", "uk": "uk", "el": "el"}.get(language, "de")


async def deliver_due_reminders(
    repository: ReminderRepository,
    store: Any,
    *,
    send_text: Callable[[str, str], Awaitable[Any]],
    send_template: Callable[[str, str, str, list[str]], Awaitable[Any]],
    now: datetime | None = None,
    template_name: str = "",
    limit: int = 10,
) -> dict[str, int]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    outcome = {"claimed": 0, "sent": 0, "blocked": 0, "failed": 0}
    for reminder in repository.claim_due(now=current, limit=limit):
        outcome["claimed"] += 1
        reminder_id = str(reminder.get("reminder_id") or "")
        try:
            recipient = decrypt_recipient(str(reminder.get("recipient_ciphertext") or ""))
            profile = store.get_user(recipient)
            language = str(reminder.get("language") or profile.get("preferred_language") or "de")
            title = str(reminder.get("title") or "Follow-up")
            if service_window_open(profile, now=current):
                await send_text(recipient, render_reminder(language, title))
            elif template_name:
                first_name = str(profile.get("first_name") or "") or {
                    "ar": "صديقنا", "de": "Hallo", "en": "Hello", "uk": "Вітаю", "el": "Γεια",
                }.get(language, "Hallo")
                scheduled = _parse_datetime(reminder.get("scheduled_at")) or current
                local_date = scheduled.astimezone(ZoneInfo(str(reminder.get("timezone") or DEFAULT_TIMEZONE))).strftime("%d.%m.%Y")
                await send_template(recipient, template_name, template_language(language), [first_name, title, local_date])
            else:
                repository.mark_blocked(reminder_id, now=current)
                outcome["blocked"] += 1
                continue
            repository.mark_sent(reminder_id, now=current)
            outcome["sent"] += 1
        except Exception as exc:  # delivery boundary must never stop the worker
            logger.exception("Reminder delivery failed", extra={"reminder_id": reminder_id})
            repository.mark_failed(
                reminder_id,
                error=exc.__class__.__name__,
                attempt_count=int(reminder.get("attempt_count") or 1),
                now=current,
            )
            outcome["failed"] += 1
    return outcome


async def reminder_worker_loop(
    repository: ReminderRepository,
    store: Any,
    *,
    send_text: Callable[[str, str], Awaitable[Any]],
    send_template: Callable[[str, str, str, list[str]], Awaitable[Any]],
    stop_event: asyncio.Event,
    interval_seconds: int = 60,
    template_name: str = "",
) -> None:
    delay = max(30, min(int(interval_seconds), 3600))
    while not stop_event.is_set():
        try:
            await deliver_due_reminders(
                repository,
                store,
                send_text=send_text,
                send_template=send_template,
                template_name=template_name,
            )
        except Exception:
            logger.exception("Reminder worker iteration failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
        except TimeoutError:
            pass


def reminder_needs_date_message(language: str) -> str:
    return {
        "ar": "أكيد. قلّي الموعد بصيغة واضحة مثل «ذكرني يوم 10.08.2026»، أو سجّل مهلة للمهمة أولًا وبعدها قل «ذكرني قبلها بيوم». واجب علي ما أضيّعلك الموعد 😉",
        "de": "Gern. Nenne ein Datum wie „Erinnere mich am 10.08.2026“ oder speichere zuerst die Frist der Aufgabe und schreib danach „einen Tag vorher erinnern“.",
        "en": "Sure. Give me a date such as “remind me on 10.08.2026”, or save the task deadline first and then say “remind me one day before”.",
        "uk": "Звісно. Напиши дату, наприклад «нагадай 10.08.2026», або спочатку збережи термін завдання.",
        "el": "Βεβαίως. Δώσε ημερομηνία, π.χ. «θύμισέ μου στις 10.08.2026», ή αποθήκευσε πρώτα την προθεσμία της εργασίας.",
    }.get(language, "Please provide a reminder date.")


_WEEKDAY_NAMES = {
    "ar": ("الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"),
    "de": ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"),
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "uk": ("понеділок", "вівторок", "середа", "четвер", "п’ятниця", "субота", "неділя"),
    "el": ("Δευτέρα", "Τρίτη", "Τετάρτη", "Πέμπτη", "Παρασκευή", "Σάββατο", "Κυριακή"),
}


def _recurrence_weekday_label(language: str, reminder: dict[str, Any]) -> str:
    weekdays = _parse_recurrence_weekdays(reminder.get("recurrence_weekdays")) or ()
    names = _WEEKDAY_NAMES.get(language, _WEEKDAY_NAMES["en"])
    selected = [names[weekday] for weekday in weekdays]
    if len(selected) < 2:
        return selected[0] if selected else ""
    connector = {"ar": " و", "de": " und ", "en": " and ", "uk": " і ", "el": " και "}.get(
        language, " and "
    )
    return ", ".join(selected[:-1]) + connector + selected[-1]


def _holiday_scope_note(language: str, reminder: dict[str, Any]) -> str:
    region = canonical_german_state_code(reminder.get("holiday_region"))
    if not region:
        return ""
    label = german_state_label(region, language)
    return {
        "ar": f" وسأتجاوز العطل الرسمية العامة في ولاية {label}.",
        "de": f" Landesweite gesetzliche Feiertage in {label} werden ausgelassen.",
        "en": f" State-wide public holidays in {label} are skipped.",
        "uk": f" Загальнодержавні свята землі {label} пропускаються.",
        "el": f" Οι επίσημες αργίες σε επίπεδο κρατιδίου {label} παραλείπονται.",
    }.get(language, f" State-wide public holidays in {label} are skipped.")


def reminder_created_message(language: str, reminder: dict[str, Any]) -> str:
    when = _parse_datetime(reminder.get("scheduled_at")) or datetime.now(UTC)
    local = when.astimezone(ZoneInfo(str(reminder.get("timezone") or DEFAULT_TIMEZONE)))
    date_text = local.strftime("%d.%m.%Y, %H:%M")
    title = str(reminder.get("title") or "")
    remaining = int(reminder.get("recurrence_remaining") or 0)
    days = int(reminder.get("recurrence_days") or 0)
    if remaining > 1 and days > 0:
        weekday_label = _recurrence_weekday_label(language, reminder)
        if weekday_label:
            recurrence = {
                "ar": f" ويتكرر أيام {weekday_label}، {remaining} مرات.",
                "de": f" Wiederholung am {weekday_label}, insgesamt {remaining}-mal.",
                "en": f" It repeats on {weekday_label}, {remaining} times total.",
                "uk": f" Повторення у дні: {weekday_label}, усього {remaining} разів.",
                "el": f" Επανάληψη κάθε {weekday_label}, συνολικά {remaining} φορές.",
            }.get(language, f" Repeats on {weekday_label}, {remaining} times.")
        elif reminder.get("weekdays_only"):
            recurrence = {
                "ar": f" ويتكرر في أيام العمل فقط، {remaining} مرات.",
                "de": f" Wiederholung nur werktags, insgesamt {remaining}-mal.",
                "en": f" It repeats on weekdays only, {remaining} times total.",
                "uk": f" Повторення лише в робочі дні, усього {remaining} разів.",
                "el": f" Επανάληψη μόνο τις εργάσιμες ημέρες, συνολικά {remaining} φορές.",
            }.get(language, f" Repeats on weekdays only, {remaining} times.")
        else:
            recurrence = {
                "ar": f" ويتكرر كل {days} يوم، {remaining} مرات.",
                "de": f" Wiederholung alle {days} Tag(e), insgesamt {remaining}-mal.",
                "en": f" It repeats every {days} day(s), {remaining} times total.",
                "uk": f" Повторення кожні {days} дн., усього {remaining} разів.",
                "el": f" Επανάληψη κάθε {days} ημέρα/ες, συνολικά {remaining} φορές.",
            }.get(language, f" Repeats every {days} day(s), {remaining} times.")
    else:
        recurrence = ""
    holiday_note = _holiday_scope_note(language, reminder)
    return {
        "ar": f"تم ✅ رح ذكّرك بخصوص «{title}» بتاريخ {date_text}.{recurrence}{holiday_note} ما في رسائل دعائية، والتذكير بتقدر تلغيه بأي وقت.",
        "de": f"Gespeichert ✅ Ich erinnere dich am {date_text} an „{title}“.{recurrence}{holiday_note} Keine Werbung, und du kannst die Erinnerung jederzeit löschen.",
        "en": f"Saved ✅ I will remind you about “{title}” on {date_text}.{recurrence}{holiday_note} No marketing, and you can cancel it anytime.",
        "uk": f"Збережено ✅ Нагадаю про «{title}» {date_text}.{recurrence}{holiday_note} Без реклами; нагадування можна скасувати будь-коли.",
        "el": f"Αποθηκεύτηκε ✅ Θα σου θυμίσω το «{title}» στις {date_text}.{recurrence}{holiday_note} Χωρίς διαφημίσεις και μπορείς να το ακυρώσεις οποτεδήποτε.",
    }.get(language, f"Reminder saved for {date_text}.")


def reminder_list_message(language: str, reminders: list[dict[str, Any]]) -> str:
    if not reminders:
        return {
            "ar": "ما عندك تذكيرات فعّالة حاليًا.", "de": "Du hast derzeit keine aktiven Erinnerungen.",
            "en": "You have no active reminders.", "uk": "Активних нагадувань немає.",
            "el": "Δεν έχεις ενεργές υπενθυμίσεις.",
        }.get(language, "No active reminders.")
    lines = []
    for index, reminder in enumerate(reminders, start=1):
        when = _parse_datetime(reminder.get("scheduled_at")) or datetime.now(UTC)
        local = when.astimezone(ZoneInfo(str(reminder.get("timezone") or DEFAULT_TIMEZONE)))
        repeat = ""
        if int(reminder.get("recurrence_remaining") or 0) > 1:
            weekday_label = _recurrence_weekday_label(language, reminder)
            if weekday_label:
                repeat = f" ↻ {reminder.get('recurrence_remaining')}×/{weekday_label}"
            elif reminder.get("weekdays_only"):
                label = {
                    "ar": "أيام العمل", "de": "werktags", "en": "weekdays",
                    "uk": "робочі дні", "el": "εργάσιμες",
                }.get(language, "weekdays")
                repeat = f" ↻ {reminder.get('recurrence_remaining')}×/{label}"
            else:
                repeat = f" ↻ {reminder.get('recurrence_remaining')}×/{reminder.get('recurrence_days')}d"
        region = canonical_german_state_code(reminder.get("holiday_region"))
        if region:
            repeat += f" · ⏭ {german_state_label(region, language)}"
        lines.append(f"{index}. {reminder.get('title')} — {local.strftime('%d.%m.%Y %H:%M')}{repeat}")
    heading = {
        "ar": "تذكيراتك الفعّالة:", "de": "Deine aktiven Erinnerungen:", "en": "Your active reminders:",
        "uk": "Твої активні нагадування:", "el": "Οι ενεργές υπενθυμίσεις σου:",
    }.get(language, "Active reminders:")
    return heading + "\n" + "\n".join(lines)


def reminder_cancelled_message(language: str, count: int) -> str:
    if count <= 0:
        return {
            "ar": "ما لقيت تذكير فعّال حتى ألغيه.", "de": "Ich habe keine aktive Erinnerung zum Löschen gefunden.",
            "en": "I found no active reminder to cancel.", "uk": "Активного нагадування для скасування немає.",
            "el": "Δεν βρήκα ενεργή υπενθύμιση για ακύρωση.",
        }.get(language, "No active reminder found.")
    return {
        "ar": f"تمام، ألغيت {count} تذكير ✅", "de": f"Erledigt, {count} Erinnerung(en) gelöscht ✅",
        "en": f"Done, {count} reminder(s) cancelled ✅", "uk": f"Готово, скасовано нагадувань: {count} ✅",
        "el": f"Έγινε, ακυρώθηκαν {count} υπενθυμίσεις ✅",
    }.get(language, f"Cancelled {count} reminder(s).")


def reminder_rescheduled_message(language: str, reminder: dict[str, Any]) -> str:
    when = _parse_datetime(reminder.get("scheduled_at")) or datetime.now(UTC)
    local = when.astimezone(ZoneInfo(str(reminder.get("timezone") or DEFAULT_TIMEZONE)))
    date_text = local.strftime("%d.%m.%Y, %H:%M")
    title = str(reminder.get("title") or "")
    return {
        "ar": f"تمام ✅ أجّلت «{title}» للموعد {date_text}.",
        "de": f"Erledigt ✅ „{title}“ wurde auf den {date_text} verschoben.",
        "en": f"Done ✅ “{title}” was moved to {date_text}.",
        "uk": f"Готово ✅ «{title}» перенесено на {date_text}.",
        "el": f"Έγινε ✅ Το «{title}» μεταφέρθηκε στις {date_text}.",
    }.get(language, f"Reminder moved to {date_text}.")


def reminder_selection_message(language: str, reminders: list[dict[str, Any]]) -> str:
    prefix = {
        "ar": "عندك أكثر من تذكير. حدّد الرقم، مثل: «أجّل التذكير 2 لمدة 10 دقائق».\n",
        "de": "Du hast mehrere Erinnerungen. Nenne die Nummer, z. B. „Erinnerung 2 um 10 Minuten verschieben“.\n",
        "en": "You have several reminders. Include the number, for example: “snooze reminder 2 for 10 minutes”.\n",
        "uk": "У вас кілька нагадувань. Вкажіть номер, наприклад: «перенеси нагадування 2 на 10 хвилин».\n",
        "el": "Έχεις πολλές υπενθυμίσεις. Βάλε τον αριθμό, π.χ. «μετάφερε την υπενθύμιση 2 κατά 10 λεπτά».\n",
    }.get(language, "Choose a reminder number.\n")
    return prefix + reminder_list_message(language, reminders)


def reminder_reschedule_conflict_message(language: str) -> str:
    return {
        "ar": "عندك تذكير مطابق بهذا الموعد أصلًا، لذلك ما غيّرت شي.",
        "de": "Für diesen Zeitpunkt gibt es bereits dieselbe Erinnerung; ich habe nichts geändert.",
        "en": "The same reminder already exists at that time, so I changed nothing.",
        "uk": "На цей час уже є таке саме нагадування, тому нічого не змінено.",
        "el": "Υπάρχει ήδη η ίδια υπενθύμιση για τότε, οπότε δεν άλλαξα τίποτα.",
    }.get(language, "The same reminder already exists at that time.")


def reminder_cancel_selection_message(language: str, reminders: list[dict[str, Any]]) -> str:
    prefix = {
        "ar": "عندك أكثر من تذكير. حدّد الرقم أو الأرقام، مثل: «ألغي التذكير 1 و2».\n",
        "de": "Du hast mehrere Erinnerungen. Nenne die Nummern, z. B. „Erinnerung 1 und 2 löschen“.\n",
        "en": "You have several reminders. Include the numbers, for example: “cancel reminders 1 and 2”.\n",
        "uk": "У вас кілька нагадувань. Вкажіть номери, наприклад: «скасуй нагадування 1 і 2».\n",
        "el": "Έχεις πολλές υπενθυμίσεις. Βάλε τους αριθμούς, π.χ. «ακύρωσε τις υπενθυμίσεις 1 και 2».\n",
    }.get(language, "Choose one or more reminder numbers.\n")
    return prefix + reminder_list_message(language, reminders)


def reminder_recurrence_updated_message(language: str, reminder: dict[str, Any]) -> str:
    title = str(reminder.get("title") or "")
    days = int(reminder.get("recurrence_days") or 0)
    count = int(reminder.get("recurrence_remaining") or 0)
    holiday_note = _holiday_scope_note(language, reminder)
    if days and count:
        weekday_label = _recurrence_weekday_label(language, reminder)
        if weekday_label:
            message = {
                "ar": f"تمام ✅ صار تذكير «{title}» يتكرر أيام {weekday_label}، {count} مرات.",
                "de": f"Erledigt ✅ „{title}“ wiederholt sich am {weekday_label}, insgesamt {count}-mal.",
                "en": f"Done ✅ “{title}” now repeats on {weekday_label}, {count} times.",
                "uk": f"Готово ✅ «{title}» повторюється у дні: {weekday_label}, {count} разів.",
                "el": f"Έγινε ✅ Το «{title}» επαναλαμβάνεται κάθε {weekday_label}, {count} φορές.",
            }.get(language, f"Specific weekday recurrence updated for {title}.")
            return message + holiday_note
        if reminder.get("weekdays_only"):
            message = {
                "ar": f"تمام ✅ صار تذكير «{title}» يتكرر في أيام العمل فقط، {count} مرات.",
                "de": f"Erledigt ✅ „{title}“ wiederholt sich nur werktags, insgesamt {count}-mal.",
                "en": f"Done ✅ “{title}” now repeats on weekdays only, {count} times.",
                "uk": f"Готово ✅ «{title}» повторюється лише в робочі дні, {count} разів.",
                "el": f"Έγινε ✅ Το «{title}» επαναλαμβάνεται μόνο τις εργάσιμες ημέρες, {count} φορές.",
            }.get(language, f"Weekday recurrence updated for {title}.")
            return message + holiday_note
        message = {
            "ar": f"تمام ✅ صار تذكير «{title}» يتكرر كل {days} يوم، {count} مرات.",
            "de": f"Erledigt ✅ „{title}“ wiederholt sich alle {days} Tag(e), insgesamt {count}-mal.",
            "en": f"Done ✅ “{title}” now repeats every {days} day(s), {count} times.",
            "uk": f"Готово ✅ «{title}» повторюється кожні {days} дн., {count} разів.",
            "el": f"Έγινε ✅ Το «{title}» επαναλαμβάνεται κάθε {days} ημέρα/ες, {count} φορές.",
        }.get(language, f"Recurrence updated for {title}.")
        return message + holiday_note
    return {
        "ar": f"تمام ✅ وقفت تكرار «{title}» وخليته مرة واحدة.",
        "de": f"Erledigt ✅ Die Wiederholung für „{title}“ wurde beendet.",
        "en": f"Done ✅ Repetition was stopped for “{title}”.",
        "uk": f"Готово ✅ Повторення «{title}» зупинено.",
        "el": f"Έγινε ✅ Η επανάληψη για το «{title}» σταμάτησε.",
    }.get(language, f"Recurrence stopped for {title}.")


def reminder_recurrence_selection_message(language: str, reminders: list[dict[str, Any]]) -> str:
    prefix = {
        "ar": "عندك أكثر من تذكير. حدّد الرقم، مثل: «وقف تكرار التذكير 2».",
        "de": "Du hast mehrere Erinnerungen. Nenne die Nummer, z. B. „Wiederholung für Erinnerung 2 stoppen“.",
        "en": "You have several reminders. Include the number, for example: “stop repeating reminder 2”.",
        "uk": "У вас кілька нагадувань. Вкажіть номер нагадування.",
        "el": "Έχεις πολλές υπενθυμίσεις. Βάλε τον αριθμό της υπενθύμισης.",
    }.get(language, "Choose a reminder number.")
    return prefix + "\n" + reminder_list_message(language, reminders)
