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
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
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
    def _dedupe(phone_hash: str, mission_id: str, scheduled_at: datetime, title: str) -> str:
        raw = f"{phone_hash}|{mission_id}|{scheduled_at.astimezone(UTC).isoformat()}|{title.casefold()}"
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
    ) -> dict[str, Any]:
        when = scheduled_at if scheduled_at.tzinfo else scheduled_at.replace(tzinfo=UTC)
        when = when.astimezone(UTC)
        phone_hash = _phone_hash(phone)
        clean_title = _clean(title, 180) or "Follow-up"
        clean_mission = _clean(mission_id, 64)
        lang = language if language in SUPPORTED_LANGUAGES else "de"
        reminder = {
            "reminder_id": uuid4().hex,
            "dedupe_key": self._dedupe(phone_hash, clean_mission, when, clean_title),
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
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                row = connection.execute(
                    """
                    INSERT INTO hero_reminders
                        (reminder_id, dedupe_key, phone_hash, recipient_ciphertext, mission_id,
                         title, language, timezone, scheduled_at, next_attempt_at)
                    VALUES (%s, %s, %s, %s, NULLIF(%s, ''), %s, %s, %s, %s, %s)
                    ON CONFLICT (dedupe_key) DO UPDATE
                    SET updated_at = NOW()
                    RETURNING *
                    """,
                    (
                        reminder["reminder_id"], reminder["dedupe_key"], phone_hash,
                        reminder["recipient_ciphertext"], clean_mission, clean_title, lang,
                        reminder["timezone"], when, when,
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
        self._set_delivery_state(reminder_id, "sent", current, "", None, sent_at=current)

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


def reminder_created_message(language: str, reminder: dict[str, Any]) -> str:
    when = _parse_datetime(reminder.get("scheduled_at")) or datetime.now(UTC)
    local = when.astimezone(ZoneInfo(str(reminder.get("timezone") or DEFAULT_TIMEZONE)))
    date_text = local.strftime("%d.%m.%Y, %H:%M")
    title = str(reminder.get("title") or "")
    return {
        "ar": f"تم ✅ رح ذكّرك بخصوص «{title}» بتاريخ {date_text}. ما في رسائل دعائية، والتذكير بتقدر تلغيه بأي وقت.",
        "de": f"Gespeichert ✅ Ich erinnere dich am {date_text} an „{title}“. Keine Werbung, und du kannst die Erinnerung jederzeit löschen.",
        "en": f"Saved ✅ I will remind you about “{title}” on {date_text}. No marketing, and you can cancel it anytime.",
        "uk": f"Збережено ✅ Нагадаю про «{title}» {date_text}. Без реклами; нагадування можна скасувати будь-коли.",
        "el": f"Αποθηκεύτηκε ✅ Θα σου θυμίσω το «{title}» στις {date_text}. Χωρίς διαφημίσεις και μπορείς να το ακυρώσεις οποτεδήποτε.",
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
        lines.append(f"{index}. {reminder.get('title')} — {local.strftime('%d.%m.%Y %H:%M')}")
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
