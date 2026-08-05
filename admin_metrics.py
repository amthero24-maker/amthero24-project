"""Privacy-safe aggregate metrics for AmtHero24 operators."""
from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    return current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(((str(key or "unknown"), int(value)) for key, value in counter.items()), key=lambda item: item[0]))


_ACTIVE_REMINDER_STATUSES = {"pending", "failed", "blocked_template", "processing"}


def _safe_error_code(value: Any) -> str:
    raw = str(value or "").strip().casefold()
    if not raw:
        return ""
    if not re.fullmatch(r"[a-z][a-z0-9_:-]{0,79}", raw):
        return "redacted"
    return re.sub(r"\d+", "n", raw)


def _safe_nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _safe_timestamp(value: Any) -> str | None:
    parsed = _as_datetime(value)
    return parsed.isoformat() if parsed is not None else None


def _latest_reminder(record: Any) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    return {
        "status": _safe_error_code(record.get("status")) or "unknown",
        "scheduled_at": _safe_timestamp(record.get("scheduled_at")),
        "attempt_count": _safe_nonnegative_int(record.get("attempt_count")),
        "last_error_code": _safe_error_code(record.get("last_error")),
        "next_attempt_at": _safe_timestamp(record.get("next_attempt_at")),
        "lease_until": _safe_timestamp(record.get("lease_until")),
        "sent_at": _safe_timestamp(record.get("sent_at")),
    }


def _json_reminder_diagnostics(reminders: list[dict[str, Any]], current: datetime) -> dict[str, Any]:
    due_unsent = 0
    unsent_recipients: set[str] = set()
    for reminder in reminders:
        status = str(reminder.get("status") or "unknown")
        if reminder.get("sent_at") is None and status != "cancelled":
            recipient = str(reminder.get("phone_hash") or "")
            if recipient:
                unsent_recipients.add(recipient)
        scheduled = _as_datetime(reminder.get("scheduled_at"))
        next_attempt = _as_datetime(reminder.get("next_attempt_at")) or scheduled
        lease = _as_datetime(reminder.get("lease_until"))
        if (
            status in _ACTIVE_REMINDER_STATUSES
            and scheduled is not None
            and next_attempt is not None
            and scheduled <= current
            and next_attempt <= current
            and (lease is None or lease < current)
        ):
            due_unsent += 1
    latest = max(
        reminders,
        key=lambda item: _as_datetime(item.get("created_at")) or datetime.min.replace(tzinfo=UTC),
        default=None,
    )
    return {
        "due_unsent": due_unsent,
        "unsent_recipients": len(unsent_recipients),
        "latest": _latest_reminder(latest),
    }


def _json_overview(store: Any, current: datetime) -> dict[str, Any]:
    snapshot = store.snapshot()
    users = [record for record in snapshot.get("users", {}).values() if isinstance(record, dict)]
    missions = [record for record in snapshot.get("cases", {}).values() if isinstance(record, dict)]
    reminders = [record for record in snapshot.get("reminders", {}).values() if isinstance(record, dict)]
    messages = [record for record in snapshot.get("messages", {}).values() if isinstance(record, dict)]
    pending = [record for record in snapshot.get("pending_document_actions", {}).values() if isinstance(record, dict)]

    languages = Counter(str(user.get("preferred_language") or user.get("session_language") or "unknown") for user in users)
    consent = Counter(str(user.get("memory_consent") or "not_set") for user in users)
    active_24h = 0
    active_7d = 0
    for user in users:
        seen = _as_datetime(user.get("last_seen") or user.get("updated_at"))
        if not seen:
            continue
        if seen >= current - timedelta(hours=24):
            active_24h += 1
        if seen >= current - timedelta(days=7):
            active_7d += 1

    mission_status = Counter(str(item.get("status") or "unknown") for item in missions)
    mission_topics = Counter(str(item.get("topic") or "unknown") for item in missions)
    reminder_status = Counter(str(item.get("status") or "unknown") for item in reminders)

    recent_messages = []
    for message in messages:
        created = _as_datetime(message.get("created_at"))
        if created and created >= current - timedelta(hours=24):
            recent_messages.append(message)
    message_status = Counter(str(item.get("status") or "unknown") for item in recent_messages)
    message_types = Counter(str(item.get("message_type") or item.get("type") or "unknown") for item in recent_messages)

    deletion_events = [
        event for event in snapshot.get("privacy_events", [])
        if isinstance(event, dict)
        and (_as_datetime(event.get("created_at")) or datetime.min.replace(tzinfo=UTC)) >= current - timedelta(days=30)
    ]
    return {
        "users": {
            "total": len(users),
            "active_24h": active_24h,
            "active_7d": active_7d,
            "languages": _counter_dict(languages),
            "memory_consent": _counter_dict(consent),
        },
        "missions": {
            "total": len(missions),
            "by_status": _counter_dict(mission_status),
            "by_topic": _counter_dict(mission_topics),
        },
        "reminders": {
            "total": len(reminders),
            "by_status": _counter_dict(reminder_status),
            **_json_reminder_diagnostics(reminders, current),
        },
        "messages_24h": {
            "total": len(recent_messages),
            "by_status": _counter_dict(message_status),
            "by_type": _counter_dict(message_types),
            "failed": int(message_status.get("failed", 0)),
        },
        "document_actions": {"pending": len(pending)},
        "privacy": {"deletions_30d": len(deletion_events)},
    }


def _postgres_overview(store: Any, current: datetime) -> dict[str, Any]:
    languages: Counter[str] = Counter()
    consent: Counter[str] = Counter()
    active_24h = 0
    active_7d = 0
    with store.pool.connection() as connection:
        user_rows = connection.execute("SELECT profile, updated_at FROM hero_users").fetchall()
        for row in user_rows:
            profile = dict(row["profile"] or {})
            languages[str(profile.get("preferred_language") or profile.get("session_language") or "unknown")] += 1
            consent[str(profile.get("memory_consent") or "not_set")] += 1
            seen = _as_datetime(profile.get("last_seen") or row.get("updated_at"))
            if seen and seen >= current - timedelta(hours=24):
                active_24h += 1
            if seen and seen >= current - timedelta(days=7):
                active_7d += 1

        mission_rows = connection.execute(
            "SELECT status, topic, COUNT(*) AS count FROM hero_missions GROUP BY status, topic"
        ).fetchall()
        reminder_rows = connection.execute(
            "SELECT status, COUNT(*) AS count FROM hero_reminders GROUP BY status"
        ).fetchall()
        reminder_due_row = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM hero_reminders
            WHERE status = ANY(%s)
              AND scheduled_at <= %s
              AND next_attempt_at <= %s
              AND (lease_until IS NULL OR lease_until < %s)
            """,
            (list(_ACTIVE_REMINDER_STATUSES), current, current, current),
        ).fetchone()
        reminder_recipient_row = connection.execute(
            """
            SELECT COUNT(DISTINCT phone_hash) AS count
            FROM hero_reminders
            WHERE sent_at IS NULL AND status <> 'cancelled'
            """
        ).fetchone()
        latest_reminder_row = connection.execute(
            """
            SELECT status, scheduled_at, attempt_count, last_error,
                   next_attempt_at, lease_until, sent_at
            FROM hero_reminders
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        message_rows = connection.execute(
            """
            SELECT status, message_type, COUNT(*) AS count
            FROM inbound_messages
            WHERE created_at >= %s
            GROUP BY status, message_type
            """,
            (current - timedelta(hours=24),),
        ).fetchall()
        pending_row = connection.execute(
            "SELECT COUNT(*) AS count FROM pending_document_actions WHERE expires_at > %s",
            (current,),
        ).fetchone()
        privacy_table = connection.execute("SELECT to_regclass('privacy_deletion_events') AS table_name").fetchone()
        if privacy_table and privacy_table.get("table_name"):
            deletion_row = connection.execute(
                "SELECT COUNT(*) AS count FROM privacy_deletion_events WHERE created_at >= %s",
                (current - timedelta(days=30),),
            ).fetchone()
            deletion_count = int(deletion_row["count"] if deletion_row else 0)
        else:
            deletion_count = 0

    mission_status: Counter[str] = Counter()
    mission_topics: Counter[str] = Counter()
    for row in mission_rows:
        count = int(row["count"])
        mission_status[str(row["status"] or "unknown")] += count
        mission_topics[str(row["topic"] or "unknown")] += count
    reminder_status = Counter({str(row["status"] or "unknown"): int(row["count"]) for row in reminder_rows})
    message_status: Counter[str] = Counter()
    message_types: Counter[str] = Counter()
    for row in message_rows:
        count = int(row["count"])
        message_status[str(row["status"] or "unknown")] += count
        message_types[str(row["message_type"] or "unknown")] += count

    return {
        "users": {
            "total": len(user_rows),
            "active_24h": active_24h,
            "active_7d": active_7d,
            "languages": _counter_dict(languages),
            "memory_consent": _counter_dict(consent),
        },
        "missions": {
            "total": sum(mission_status.values()),
            "by_status": _counter_dict(mission_status),
            "by_topic": _counter_dict(mission_topics),
        },
        "reminders": {
            "total": sum(reminder_status.values()),
            "by_status": _counter_dict(reminder_status),
            "due_unsent": int(reminder_due_row["count"] if reminder_due_row else 0),
            "unsent_recipients": int(reminder_recipient_row["count"] if reminder_recipient_row else 0),
            "latest": _latest_reminder(dict(latest_reminder_row)) if latest_reminder_row else None,
        },
        "messages_24h": {
            "total": sum(message_status.values()),
            "by_status": _counter_dict(message_status),
            "by_type": _counter_dict(message_types),
            "failed": int(message_status.get("failed", 0)),
        },
        "document_actions": {"pending": int(pending_row["count"] if pending_row else 0)},
        "privacy": {"deletions_30d": deletion_count},
    }


def build_overview(
    store: Any,
    *,
    now: datetime | None = None,
    version: str,
    model: str,
) -> dict[str, Any]:
    """Return aggregate product health with no names, phones, message text, or document content."""
    current = _now(now)
    backend = str(getattr(store, "backend_name", "json"))
    metrics = _postgres_overview(store, current) if backend == "postgresql" else _json_overview(store, current)
    return {
        "generated_at": current.isoformat(),
        "version": version,
        "storage_backend": backend,
        "text_model": model,
        **metrics,
    }


def contains_personal_fields(payload: Any) -> bool:
    """Defense-in-depth check used by tests and the API boundary."""
    forbidden = {
        "first_name", "city", "phone", "phone_hash", "sender", "recipient_ciphertext",
        "last_message", "last_assistant_reply", "conversation_summary", "raw_text", "text",
    }
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized = str(key).casefold()
            if normalized in forbidden:
                return True
            if normalized == "by_type" and isinstance(value, dict):
                # Values such as {"text": 12} are aggregate categories, not
                # message-content fields. Only non-negative integer counters may
                # use this exception; arbitrary scalar or nested data stays blocked.
                if any(
                    isinstance(item, bool) or not isinstance(item, int) or item < 0
                    for item in value.values()
                ):
                    return True
                continue
            if contains_personal_fields(value):
                return True
        return False
    if isinstance(payload, list):
        return any(contains_personal_fields(item) for item in payload)
    return False
