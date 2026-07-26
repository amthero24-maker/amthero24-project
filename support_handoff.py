"""Explicit, encrypted human-support handoff without conversation storage."""
from __future__ import annotations

import base64
import hashlib
import os
import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

SUPPORTED_LANGUAGES = {"de", "ar", "en", "uk", "el"}
OPEN_STATUSES = {"open", "assigned"}
ADMIN_STATUSES = {"assigned", "resolved", "cancelled"}


class SupportServiceError(RuntimeError):
    """Stable support handoff error category."""


@dataclass(frozen=True)
class SupportIntent:
    action: str


def support_enabled() -> bool:
    return os.getenv("HUMAN_SUPPORT_ENABLED", "false").strip().casefold() in {"1", "true", "yes", "on"}


def support_configured() -> bool:
    return support_enabled() and bool(os.getenv("SUPPORT_API_TOKEN", "").strip()) and bool(os.getenv("SUPPORT_ENCRYPTION_KEY", "").strip())


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    return current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)


def _as_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _phone_hash(phone: str) -> str:
    normalized = "".join(character for character in str(phone or "") if character.isdigit() or character == "+")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _fernet() -> Fernet:
    secret = os.getenv("SUPPORT_ENCRYPTION_KEY", "").strip()
    if not secret:
        raise SupportServiceError("missing_encryption_key")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_contact(phone: str) -> str:
    if not phone:
        raise SupportServiceError("missing_contact")
    return _fernet().encrypt(phone.encode("utf-8")).decode("ascii")


def decrypt_contact(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(str(ciphertext).encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise SupportServiceError("contact_decryption_failed") from exc


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text or "")).casefold().strip()
    value = value.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا"}))
    value = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", value)
    value = re.sub(r"[؟،؛!?.,:;]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


_REQUEST_PATTERNS = (
    "بدي احكي مع شخص", "بدي موظف", "دعم بشري", "احكي مع انسان", "تواصل مع الدعم", "ساعدني شخص",
    "mit einem menschen sprechen", "menschlicher support", "mitarbeiter sprechen", "support kontaktieren",
    "talk to a person", "human support", "contact support", "speak to an agent",
    "поговорити з людиною", "підтримка людини", "зв'язатися з підтримкою",
    "μιλησω με ανθρωπο", "ανθρωπινη υποστηριξη", "επικοινωνια με υποστηριξη",
)
_STATUS_PATTERNS = (
    "وين طلب الدعم", "حالة طلب الدعم", "شو صار بطلب الدعم", "support status", "status meiner supportanfrage",
    "my support request", "статус підтримки", "κατασταση υποστηριξης",
)
_CANCEL_PATTERNS = (
    "الغي طلب الدعم", "الغ طلب الدعم", "ما بدي دعم بشري", "supportanfrage stornieren",
    "cancel support request", "скасувати запит підтримки", "ακυρωση αιτηματος υποστηριξης",
)


def detect_support_intent(text: str) -> SupportIntent | None:
    normalized = _normalize(text)
    if any(_normalize(pattern) in normalized for pattern in _CANCEL_PATTERNS):
        return SupportIntent("cancel")
    if any(_normalize(pattern) in normalized for pattern in _STATUS_PATTERNS):
        return SupportIntent("status")
    if any(_normalize(pattern) in normalized for pattern in _REQUEST_PATTERNS):
        return SupportIntent("request")
    return None


def classify_category(text: str) -> str:
    normalized = _normalize(text)
    categories = (
        ("privacy", ("خصوصية", "بيانات", "datenschutz", "daten", "privacy", "data deletion", "приват", "δεδομεν")),
        ("technical", ("عطل", "مشكلة تقنية", "ما عم يشتغل", "technisch", "fehler", "not working", "technical", "помилка", "τεχνικ")),
        ("document", ("رسالة", "ورقة", "مستند", "rechnung", "brief", "dokument", "document", "letter", "документ", "εγγραφο")),
        ("account", ("حساب", "ذاكرة", "account", "konto", "memory", "акаунт", "λογαριασ")),
        ("subscription", ("اشتراك", "دفع", "plan", "abo", "zahlung", "subscription", "payment", "підпис", "συνδρομ")),
    )
    for category, markers in categories:
        if any(marker in normalized for marker in markers):
            return category
    return "general"


def classify_urgency(text: str) -> str:
    normalized = _normalize(text)
    urgent = (
        "اليوم", "بكرا", "مهلة", "موعد قريب", "frist heute", "frist morgen", "dringend",
        "today", "tomorrow", "deadline", "urgent", "сьогодні", "завтра", "термін", "σημερα", "αυριο", "προθεσμια",
    )
    return "high" if any(marker in normalized for marker in urgent) else "normal"


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


class SupportRepository:
    """Minimal support queue with encrypted contact and no conversation content."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.backend_name = str(getattr(store, "backend_name", "json"))
        if self.backend_name == "postgresql":
            self._initialize_postgres_schema()

    def _initialize_postgres_schema(self) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS human_support_tickets (
                ticket_id TEXT PRIMARY KEY,
                phone_hash TEXT NOT NULL,
                contact_ciphertext TEXT NOT NULL,
                language TEXT NOT NULL DEFAULT 'de',
                category TEXT NOT NULL DEFAULT 'general',
                urgency TEXT NOT NULL DEFAULT 'normal',
                status TEXT NOT NULL DEFAULT 'open',
                source TEXT NOT NULL DEFAULT 'whatsapp',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                resolved_at TIMESTAMPTZ
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS human_support_status_created_idx
            ON human_support_tickets (status, urgency, created_at)
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS human_support_one_open_per_user_idx
            ON human_support_tickets (phone_hash) WHERE status IN ('open', 'assigned')
            """,
            """
            CREATE TABLE IF NOT EXISTS human_support_admin_events (
                event_id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
        )
        with self.store.pool.connection() as connection:
            for statement in statements:
                connection.execute(statement)

    @staticmethod
    def _public(record: dict[str, Any], *, include_contact: bool = False) -> dict[str, Any]:
        allowed = {"ticket_id", "language", "category", "urgency", "status", "source", "created_at", "updated_at", "resolved_at"}
        payload = {key: deepcopy(record[key]) for key in allowed if key in record and record[key] not in (None, "")}
        if include_contact:
            payload["contact"] = decrypt_contact(str(record.get("contact_ciphertext") or ""))
        return payload

    def create(self, phone: str, *, language: str, category: str, urgency: str) -> dict[str, Any]:
        key = _phone_hash(phone)
        lang = language if language in SUPPORTED_LANGUAGES else "de"
        clean_category = _clean(category, 40) or "general"
        clean_urgency = urgency if urgency in {"normal", "high"} else "normal"
        current = _now()
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                existing = connection.execute(
                    """
                    SELECT ticket_id, language, category, urgency, status, source, created_at, updated_at, resolved_at
                    FROM human_support_tickets
                    WHERE phone_hash = %s AND status IN ('open', 'assigned')
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (key,),
                ).fetchone()
                if existing:
                    result = self._public(dict(existing))
                    result["_operation"] = "existing"
                    return result
                row = connection.execute(
                    """
                    INSERT INTO human_support_tickets
                        (ticket_id, phone_hash, contact_ciphertext, language, category, urgency)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING ticket_id, language, category, urgency, status, source, created_at, updated_at, resolved_at
                    """,
                    (uuid4().hex, key, encrypt_contact(phone), lang, clean_category, clean_urgency),
                ).fetchone()
            result = self._public(dict(row))
            result["_operation"] = "created"
            return result

        def add(data: dict[str, Any]) -> dict[str, Any]:
            tickets = data.setdefault("support_tickets", {})
            existing = [
                record for record in tickets.values()
                if isinstance(record, dict) and record.get("phone_hash") == key and record.get("status") in OPEN_STATUSES
            ]
            if existing:
                existing.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
                result = self._public(existing[0])
                result["_operation"] = "existing"
                return result
            ticket_id = uuid4().hex
            record = {
                "ticket_id": ticket_id,
                "phone_hash": key,
                "contact_ciphertext": encrypt_contact(phone),
                "language": lang,
                "category": clean_category,
                "urgency": clean_urgency,
                "status": "open",
                "source": "whatsapp",
                "created_at": current.isoformat(),
                "updated_at": current.isoformat(),
                "resolved_at": None,
            }
            tickets[ticket_id] = record
            result = self._public(record)
            result["_operation"] = "created"
            return result

        return self.store._transaction(add)

    def latest_for_user(self, phone: str) -> dict[str, Any] | None:
        key = _phone_hash(phone)
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                row = connection.execute(
                    """
                    SELECT ticket_id, language, category, urgency, status, source, created_at, updated_at, resolved_at
                    FROM human_support_tickets WHERE phone_hash = %s ORDER BY created_at DESC LIMIT 1
                    """,
                    (key,),
                ).fetchone()
            return self._public(dict(row)) if row else None
        tickets = [
            record for record in self.store.snapshot().get("support_tickets", {}).values()
            if isinstance(record, dict) and record.get("phone_hash") == key
        ]
        tickets.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return self._public(tickets[0]) if tickets else None

    def cancel_latest(self, phone: str) -> dict[str, Any] | None:
        return self._update_latest_user(phone, "cancelled")

    def _update_latest_user(self, phone: str, status: str) -> dict[str, Any] | None:
        key = _phone_hash(phone)
        current = _now()
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                row = connection.execute(
                    """
                    WITH latest AS (
                        SELECT ticket_id FROM human_support_tickets
                        WHERE phone_hash = %s AND status IN ('open', 'assigned')
                        ORDER BY created_at DESC LIMIT 1 FOR UPDATE
                    )
                    UPDATE human_support_tickets AS ticket
                    SET status = %s, updated_at = NOW(), resolved_at = CASE WHEN %s IN ('resolved', 'cancelled') THEN NOW() ELSE NULL END
                    FROM latest WHERE ticket.ticket_id = latest.ticket_id
                    RETURNING ticket.ticket_id, ticket.language, ticket.category, ticket.urgency, ticket.status,
                              ticket.source, ticket.created_at, ticket.updated_at, ticket.resolved_at
                    """,
                    (key, status, status),
                ).fetchone()
            return self._public(dict(row)) if row else None

        def update(data: dict[str, Any]) -> dict[str, Any] | None:
            tickets = [
                record for record in data.setdefault("support_tickets", {}).values()
                if isinstance(record, dict) and record.get("phone_hash") == key and record.get("status") in OPEN_STATUSES
            ]
            if not tickets:
                return None
            tickets.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
            ticket = tickets[0]
            ticket["status"] = status
            ticket["updated_at"] = current.isoformat()
            if status in {"resolved", "cancelled"}:
                ticket["resolved_at"] = current.isoformat()
            return self._public(ticket)

        return self.store._transaction(update)

    def list_admin(self, *, status: str = "open", limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        valid = status if status in {"open", "assigned", "resolved", "cancelled", "all"} else "open"
        if self.backend_name == "postgresql":
            condition = "TRUE" if valid == "all" else "status = %s"
            params: tuple[Any, ...] = () if valid == "all" else (valid,)
            with self.store.pool.connection() as connection:
                rows = connection.execute(
                    f"""
                    SELECT ticket_id, contact_ciphertext, language, category, urgency, status, source,
                           created_at, updated_at, resolved_at
                    FROM human_support_tickets WHERE {condition}
                    ORDER BY CASE WHEN urgency = 'high' THEN 0 ELSE 1 END, created_at ASC LIMIT %s
                    """,
                    (*params, safe_limit),
                ).fetchall()
            return [self._public(dict(row), include_contact=True) for row in rows]
        records = [
            record for record in self.store.snapshot().get("support_tickets", {}).values()
            if isinstance(record, dict) and (valid == "all" or record.get("status") == valid)
        ]
        records.sort(key=lambda item: (0 if item.get("urgency") == "high" else 1, str(item.get("created_at", ""))))
        return [self._public(record, include_contact=True) for record in records[:safe_limit]]

    def update_admin_status(self, ticket_id: str, status: str) -> dict[str, Any] | None:
        clean_id = _clean(ticket_id, 64)
        if status not in ADMIN_STATUSES or not clean_id:
            return None
        current = _now()
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                row = connection.execute(
                    """
                    UPDATE human_support_tickets
                    SET status = %s, updated_at = NOW(), resolved_at = CASE WHEN %s IN ('resolved', 'cancelled') THEN NOW() ELSE NULL END
                    WHERE ticket_id = %s
                    RETURNING ticket_id, language, category, urgency, status, source, created_at, updated_at, resolved_at
                    """,
                    (status, status, clean_id),
                ).fetchone()
            return self._public(dict(row)) if row else None

        def update(data: dict[str, Any]) -> dict[str, Any] | None:
            ticket = data.setdefault("support_tickets", {}).get(clean_id)
            if not isinstance(ticket, dict):
                return None
            ticket["status"] = status
            ticket["updated_at"] = current.isoformat()
            if status in {"resolved", "cancelled"}:
                ticket["resolved_at"] = current.isoformat()
            return self._public(ticket)

        return self.store._transaction(update)

    def record_admin_event(self, action: str) -> None:
        event = {"event_id": uuid4().hex, "action": _clean(action, 50), "created_at": _now().isoformat()}
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                connection.execute(
                    "INSERT INTO human_support_admin_events (event_id, action) VALUES (%s, %s)",
                    (event["event_id"], event["action"]),
                )
            return

        def add(data: dict[str, Any]) -> None:
            events = data.setdefault("support_admin_events", [])
            events.append(event)
            data["support_admin_events"] = events[-5000:]

        self.store._transaction(add)

    def delete_user(self, phone: str) -> bool:
        key = _phone_hash(phone)
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                cursor = connection.execute("DELETE FROM human_support_tickets WHERE phone_hash = %s", (key,))
            return cursor.rowcount > 0

        def delete(data: dict[str, Any]) -> bool:
            tickets = data.setdefault("support_tickets", {})
            matching = [ticket_id for ticket_id, record in tickets.items() if isinstance(record, dict) and record.get("phone_hash") == key]
            for ticket_id in matching:
                tickets.pop(ticket_id, None)
            return bool(matching)

        return bool(self.store._transaction(delete))

    def cleanup(self, *, now: datetime | None = None, resolved_days: int = 90, cancelled_days: int = 30) -> int:
        current = _now(now)
        resolved_cutoff = current - timedelta(days=max(1, resolved_days))
        cancelled_cutoff = current - timedelta(days=max(1, cancelled_days))
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                cursor = connection.execute(
                    """
                    DELETE FROM human_support_tickets
                    WHERE (status = 'resolved' AND updated_at < %s)
                       OR (status = 'cancelled' AND updated_at < %s)
                    """,
                    (resolved_cutoff, cancelled_cutoff),
                )
            return max(cursor.rowcount, 0)

        def clean(data: dict[str, Any]) -> int:
            tickets = data.setdefault("support_tickets", {})
            removable = []
            for ticket_id, record in tickets.items():
                if not isinstance(record, dict):
                    continue
                updated = _as_datetime(record.get("updated_at"))
                if updated and (
                    (record.get("status") == "resolved" and updated < resolved_cutoff)
                    or (record.get("status") == "cancelled" and updated < cancelled_cutoff)
                ):
                    removable.append(ticket_id)
            for ticket_id in removable:
                tickets.pop(ticket_id, None)
            return len(removable)

        return int(self.store._transaction(clean))

    def aggregate(self) -> dict[str, Any]:
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                rows = connection.execute(
                    "SELECT status, urgency, category, COUNT(*) AS count FROM human_support_tickets GROUP BY status, urgency, category"
                ).fetchall()
            records = [dict(row) for row in rows]
        else:
            records = []
            grouped: dict[tuple[str, str, str], int] = {}
            for ticket in self.store.snapshot().get("support_tickets", {}).values():
                if not isinstance(ticket, dict):
                    continue
                key = (str(ticket.get("status") or "unknown"), str(ticket.get("urgency") or "unknown"), str(ticket.get("category") or "unknown"))
                grouped[key] = grouped.get(key, 0) + 1
            records = [{"status": key[0], "urgency": key[1], "category": key[2], "count": count} for key, count in grouped.items()]
        by_status: dict[str, int] = {}
        by_urgency: dict[str, int] = {}
        by_category: dict[str, int] = {}
        for row in records:
            count = int(row["count"])
            by_status[str(row["status"])] = by_status.get(str(row["status"]), 0) + count
            by_urgency[str(row["urgency"])] = by_urgency.get(str(row["urgency"]), 0) + count
            by_category[str(row["category"])] = by_category.get(str(row["category"]), 0) + count
        return {
            "total": sum(by_status.values()),
            "by_status": dict(sorted(by_status.items())),
            "by_urgency": dict(sorted(by_urgency.items())),
            "by_category": dict(sorted(by_category.items())),
        }


def unavailable_message(language: str) -> str:
    return {
        "ar": "الدعم البشري المباشر مو مفعّل حاليًا. أنا بكمل معك هون خطوة خطوة، وإذا الموضوع عاجل قلّي شو المهلة أو الجهة.",
        "de": "Der direkte menschliche Support ist derzeit noch nicht aktiviert. Ich helfe dir hier weiter; bei einer Frist nenne bitte Datum und Behörde.",
        "en": "Direct human support is not enabled yet. I will keep helping here; if it is urgent, tell me the deadline and authority.",
        "uk": "Пряма підтримка людиною ще не ввімкнена. Я продовжу допомагати тут; якщо терміново, напиши строк і установу.",
        "el": "Η άμεση ανθρώπινη υποστήριξη δεν έχει ενεργοποιηθεί ακόμη. Θα συνεχίσω να βοηθώ εδώ· αν είναι επείγον, γράψε την προθεσμία και την υπηρεσία.",
    }.get(language, "Direct human support is not enabled yet.")


def created_message(language: str, ticket: dict[str, Any]) -> str:
    existing = ticket.get("_operation") == "existing"
    if language == "ar":
        return "طلب الدعم البشري مسجّل أصلًا وعم ينتظر المراجعة." if existing else "تم ✅ سجّلت طلب تواصل مع الدعم البشري. ما خزّنت نص المحادثة أو المستندات، فقط وسيلة التواصل واللغة ونوع الطلب."
    if language == "de":
        return "Deine Supportanfrage wartet bereits auf Prüfung." if existing else "Erledigt ✅ Die Anfrage an den menschlichen Support ist gespeichert. Gesprächs- und Dokumentinhalte wurden nicht übernommen."
    if language == "en":
        return "Your human-support request is already waiting for review." if existing else "Done ✅ Your human-support request was saved. Conversation and document contents were not copied into the ticket."
    if language == "uk":
        return "Запит до підтримки вже очікує розгляду." if existing else "Готово ✅ Запит до підтримки збережено без тексту розмови чи документів."
    return "Το αίτημα υποστήριξης περιμένει ήδη έλεγχο." if existing else "Έγινε ✅ Το αίτημα υποστήριξης αποθηκεύτηκε χωρίς περιεχόμενο συνομιλίας ή εγγράφων."


def status_message(language: str, ticket: dict[str, Any] | None) -> str:
    if not ticket:
        return {"ar": "ما عندك طلب دعم بشري مسجّل.", "de": "Es gibt keine gespeicherte Supportanfrage.", "en": "You have no saved support request.", "uk": "Збереженого запиту до підтримки немає.", "el": "Δεν υπάρχει αποθηκευμένο αίτημα υποστήριξης."}.get(language, "No support request found.")
    status = str(ticket.get("status") or "open")
    labels = {
        "ar": {"open": "بانتظار المراجعة", "assigned": "قيد المتابعة", "resolved": "مغلقة", "cancelled": "ملغاة"},
        "de": {"open": "wartet auf Prüfung", "assigned": "in Bearbeitung", "resolved": "abgeschlossen", "cancelled": "storniert"},
        "en": {"open": "waiting for review", "assigned": "being handled", "resolved": "resolved", "cancelled": "cancelled"},
        "uk": {"open": "очікує розгляду", "assigned": "в роботі", "resolved": "вирішено", "cancelled": "скасовано"},
        "el": {"open": "αναμονή ελέγχου", "assigned": "σε επεξεργασία", "resolved": "ολοκληρώθηκε", "cancelled": "ακυρώθηκε"},
    }
    lang = language if language in SUPPORTED_LANGUAGES else "de"
    return {"ar": f"حالة طلب الدعم: {labels[lang].get(status, status)}.", "de": f"Status deiner Supportanfrage: {labels[lang].get(status, status)}.", "en": f"Support request status: {labels[lang].get(status, status)}.", "uk": f"Статус запиту: {labels[lang].get(status, status)}.", "el": f"Κατάσταση αιτήματος: {labels[lang].get(status, status)}."}[lang]


def cancelled_message(language: str, cancelled: bool) -> str:
    if cancelled:
        return {"ar": "تم إلغاء طلب الدعم البشري ✅", "de": "Die Supportanfrage wurde storniert ✅", "en": "The support request was cancelled ✅", "uk": "Запит до підтримки скасовано ✅", "el": "Το αίτημα υποστήριξης ακυρώθηκε ✅"}.get(language, "Support request cancelled.")
    return status_message(language, None)
