"""Privacy controls and retention for AmtHero24.

This module keeps user-facing deletion complete across profile, messages, missions,
reminders, and consent history. It retains only an anonymous deletion event that
cannot be connected back to a WhatsApp number.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable
from uuid import uuid4


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    return current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)


def _phone_hash(phone: str) -> str:
    normalized = "".join(character for character in str(phone or "") if character.isdigit() or character == "+")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _safe_reminder(record: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "reminder_id", "mission_id", "title", "language", "timezone", "scheduled_at",
        "status", "sent_at", "acknowledged_at", "created_at",
    }
    return {
        key: deepcopy(record[key])
        for key in allowed
        if key in record and record[key] not in (None, "")
    }


def export_user_data(store: Any, phone: str, base_payload: dict[str, Any], reminder_repository: Any) -> dict[str, Any]:
    """Add a safe reminder export without ciphertext, phone hashes, or delivery errors."""
    payload = deepcopy(base_payload or {})
    payload["reminders"] = [
        _safe_reminder(record)
        for record in reminder_repository.list(phone, active_only=False, limit=30)
        if isinstance(record, dict)
    ]
    payload["export_scope"] = "profile, missions, reminders"
    payload["exported_at"] = _now().isoformat()
    return payload


def delete_all_user_data(store: Any, phone: str) -> bool:
    """Delete every user-linked row and keep only an anonymous deletion event."""
    key = _phone_hash(phone)
    event_id = uuid4().hex
    deleted = False

    if str(getattr(store, "backend_name", "json")) == "postgresql":
        with store.pool.connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS privacy_deletion_events (
                    event_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL DEFAULT 'whatsapp',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            for table in ("hero_reminders", "hero_missions", "memory_consent_events", "inbound_messages", "hero_users"):
                # Tables are created by their respective composition layers before this method is used.
                cursor = connection.execute(f"DELETE FROM {table} WHERE phone_hash = %s", (key,))
                deleted = deleted or cursor.rowcount > 0
            connection.execute(
                "INSERT INTO privacy_deletion_events (event_id, source) VALUES (%s, 'whatsapp')",
                (event_id,),
            )
        return deleted

    def purge(data: dict[str, Any]) -> bool:
        removed = False
        users = data.setdefault("users", {})
        removed = users.pop(key, None) is not None or removed

        messages = data.setdefault("messages", {})
        for message_id in [
            message_id for message_id, record in messages.items()
            if isinstance(record, dict) and record.get("phone_hash") == key
        ]:
            del messages[message_id]
            removed = True

        cases = data.setdefault("cases", {})
        for mission_id in [
            mission_id for mission_id, record in cases.items()
            if isinstance(record, dict) and record.get("phone_hash") == key
        ]:
            del cases[mission_id]
            removed = True

        reminders = data.setdefault("reminders", {})
        for reminder_id in [
            reminder_id for reminder_id, record in reminders.items()
            if isinstance(record, dict) and record.get("phone_hash") == key
        ]:
            del reminders[reminder_id]
            removed = True

        audit_log = data.setdefault("audit_log", [])
        filtered_audit = [
            event for event in audit_log
            if not isinstance(event, dict) or event.get("phone_hash") != key
        ]
        if len(filtered_audit) != len(audit_log):
            data["audit_log"] = filtered_audit
            removed = True

        data.setdefault("privacy_events", []).append({
            "event_id": event_id,
            "action": "user_data_deleted",
            "source": "whatsapp",
            "created_at": _now().isoformat(),
        })
        data["privacy_events"] = data["privacy_events"][-5000:]
        return removed

    return bool(store._transaction(purge))


def cleanup_retention(
    store: Any,
    *,
    now: datetime | None = None,
    message_hours: int = 24,
    delivered_reminder_days: int = 90,
    failed_reminder_days: int = 30,
    completed_mission_days: int = 730,
    consent_event_days: int = 1825,
) -> dict[str, int]:
    """Apply bounded retention while preserving open missions and pending reminders."""
    current = _now(now)
    counts = {"operational": 0, "reminders": 0, "missions": 0, "consent": 0}
    counts["operational"] = int(
        store.cleanup_expired(current, max_age=timedelta(hours=max(1, message_hours)))
    )

    delivered_cutoff = current - timedelta(days=max(1, delivered_reminder_days))
    failed_cutoff = current - timedelta(days=max(1, failed_reminder_days))
    mission_cutoff = current - timedelta(days=max(30, completed_mission_days))
    consent_cutoff = current - timedelta(days=max(365, consent_event_days))

    if str(getattr(store, "backend_name", "json")) == "postgresql":
        with store.pool.connection() as connection:
            reminder_cursor = connection.execute(
                """
                DELETE FROM hero_reminders
                WHERE (status IN ('sent', 'acknowledged', 'cancelled') AND updated_at < %s)
                   OR (status IN ('failed', 'blocked_template') AND updated_at < %s)
                """,
                (delivered_cutoff, failed_cutoff),
            )
            counts["reminders"] = max(reminder_cursor.rowcount, 0)
            mission_cursor = connection.execute(
                "DELETE FROM hero_missions WHERE status = 'completed' AND completed_at < %s",
                (mission_cutoff,),
            )
            counts["missions"] = max(mission_cursor.rowcount, 0)
            consent_cursor = connection.execute(
                "DELETE FROM memory_consent_events WHERE created_at < %s",
                (consent_cutoff,),
            )
            counts["consent"] = max(consent_cursor.rowcount, 0)
        return counts

    def clean_json(data: dict[str, Any]) -> dict[str, int]:
        reminders = data.setdefault("reminders", {})
        removable_reminders: list[str] = []
        for reminder_id, record in reminders.items():
            if not isinstance(record, dict):
                continue
            updated = _as_datetime(record.get("updated_at"))
            status = str(record.get("status") or "")
            if updated and (
                (status in {"sent", "acknowledged", "cancelled"} and updated < delivered_cutoff)
                or (status in {"failed", "blocked_template"} and updated < failed_cutoff)
            ):
                removable_reminders.append(reminder_id)
        for reminder_id in removable_reminders:
            del reminders[reminder_id]

        cases = data.setdefault("cases", {})
        removable_missions = []
        for mission_id, record in cases.items():
            if not isinstance(record, dict) or record.get("status") != "completed":
                continue
            completed = _as_datetime(record.get("completed_at"))
            if completed and completed < mission_cutoff:
                removable_missions.append(mission_id)
        for mission_id in removable_missions:
            del cases[mission_id]

        audit_log = data.setdefault("audit_log", [])
        kept_audit = []
        removed_consent = 0
        for event in audit_log:
            created = _as_datetime(event.get("created_at")) if isinstance(event, dict) else None
            if created and created < consent_cutoff:
                removed_consent += 1
            else:
                kept_audit.append(event)
        data["audit_log"] = kept_audit
        return {
            "reminders": len(removable_reminders),
            "missions": len(removable_missions),
            "consent": removed_consent,
        }

    cleaned = store._transaction(clean_json)
    counts.update(cleaned)
    return counts


async def retention_worker_loop(
    store: Any,
    *,
    stop_event: asyncio.Event,
    interval_seconds: int = 21600,
    on_error: Callable[[Exception], Awaitable[None] | None] | None = None,
) -> None:
    delay = max(3600, min(int(interval_seconds), 86400))
    while not stop_event.is_set():
        try:
            cleanup_retention(
                store,
                message_hours=int(os.getenv("MESSAGE_RETENTION_HOURS", "24")),
                delivered_reminder_days=int(os.getenv("REMINDER_RETENTION_DAYS", "90")),
                failed_reminder_days=int(os.getenv("FAILED_REMINDER_RETENTION_DAYS", "30")),
                completed_mission_days=int(os.getenv("COMPLETED_MISSION_RETENTION_DAYS", "730")),
                consent_event_days=int(os.getenv("CONSENT_EVENT_RETENTION_DAYS", "1825")),
            )
        except Exception as exc:
            if on_error:
                result = on_error(exc)
                if asyncio.iscoroutine(result):
                    await result
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
        except TimeoutError:
            pass
