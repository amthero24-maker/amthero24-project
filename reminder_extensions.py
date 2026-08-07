"""Follow-up and reminder composition layer for the AmtHero24 production app."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import document_extensions as composed
import reminder_engine as reminder_module
from deployment_lifecycle import lifecycle
from encryption_policy import reminder_encryption_ready
from reminder_engine import (
    ReminderRepository,
    detect_reminder_intent,
    reminder_acknowledged_message,
    reminder_acknowledgement_not_found_message,
    reminder_acknowledgement_selection_message,
    reminder_already_acknowledged_message,
    reminder_cancelled_message,
    reminder_cancel_selection_message,
    reminder_recurrence_selection_message,
    reminder_recurrence_updated_message,
    reminder_created_message,
    reminder_list_message,
    reminder_needs_date_message,
    reminder_recipient_hash,
    reminder_reschedule_conflict_message,
    reminder_rescheduled_message,
    reminder_selection_message,
    reminder_snooze_conflict_message,
    reminder_snooze_invalid_message,
    reminder_snooze_limit_message,
    reminder_snooze_not_found_message,
    reminder_snooze_selection_message,
    reminder_snoozed_message,
    reminder_worker_loop,
    resolve_reminder_schedule,
)
from whatsapp import send_whatsapp_template

core = composed.core
_ORIGINAL_PROCESS_INCOMING = composed.process_incoming
_ORIGINAL_MISSION_CREATED_MESSAGE = core.mission_created_message
_ORIGINAL_RENDER_REMINDER = reminder_module.render_reminder
_REMINDER_REPOSITORY: ReminderRepository | None = None
_WORKER_TASK: asyncio.Task[None] | None = None
_WORKER_STOP: asyncio.Event | None = None
_WORKER_ID = uuid4().hex
logger = logging.getLogger("amthero24.reminders")


class ResilientReminderRepository(ReminderRepository):
    """Reclaim expired leases and release only this process's work during drain."""

    def _initialize_postgres_schema(self) -> None:
        super()._initialize_postgres_schema()
        with self.store.pool.connection() as connection:
            connection.execute("ALTER TABLE hero_reminders ADD COLUMN IF NOT EXISTS lease_owner TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS hero_reminders_owner_idx "
                "ON hero_reminders (lease_owner) WHERE status = 'processing'"
            )

    def claim_due(self, *, now: datetime | None = None, limit: int = 10) -> list[dict[str, Any]]:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        safe_limit = max(1, min(int(limit), 50))
        retry_statuses = ("pending", "failed", "blocked_template")
        allowed_hashes = _canary_phone_hashes() if _enabled("REMINDER_WORKER_ENABLED") else None
        allowed = None if allowed_hashes is None else sorted(allowed_hashes)
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                connection.execute(
                    """
                    UPDATE hero_reminders
                    SET status = 'failed', lease_until = NULL, lease_owner = NULL,
                        next_attempt_at = LEAST(next_attempt_at, %s),
                        last_error = 'expired_delivery_lease', updated_at = NOW()
                    WHERE status = 'processing' AND lease_until IS NOT NULL AND lease_until < %s
                      AND (%s::text[] IS NULL OR phone_hash = ANY(%s))
                    """,
                    (current, current, allowed, allowed),
                )
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
                    SET status = 'processing', lease_until = %s, lease_owner = %s,
                        attempt_count = attempt_count + 1, updated_at = NOW()
                    FROM due WHERE reminder.reminder_id = due.reminder_id
                    RETURNING reminder.*
                    """,
                    (
                        list(retry_statuses), current, current, current, allowed, allowed, safe_limit,
                        current + timedelta(minutes=5), _WORKER_ID,
                    ),
                ).fetchall()
            return [self._from_row(row) for row in rows]

        def reclaim(data: dict[str, Any]) -> None:
            for item in data.setdefault("reminders", {}).values():
                if not isinstance(item, dict) or item.get("status") != "processing" or not item.get("lease_until"):
                    continue
                if allowed_hashes is not None and item.get("phone_hash") not in allowed_hashes:
                    continue
                try:
                    lease_until = datetime.fromisoformat(str(item["lease_until"]))
                    if lease_until.tzinfo is None:
                        lease_until = lease_until.replace(tzinfo=UTC)
                except (TypeError, ValueError):
                    continue
                if lease_until.astimezone(UTC) < current:
                    item["status"] = "failed"
                    item["lease_until"] = None
                    item["next_attempt_at"] = current.isoformat()
                    item["last_error"] = "expired_delivery_lease"
                    item["updated_at"] = current.isoformat()

        self.store._transaction(reclaim)
        return super().claim_due(now=current, limit=limit, allowed_phone_hashes=allowed_hashes)

    def release_owned(self, *, now: datetime | None = None) -> int:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        if self.backend_name != "postgresql":
            return 0
        with self.store.pool.connection() as connection:
            result = connection.execute(
                """
                UPDATE hero_reminders
                SET status = 'failed', lease_until = NULL, lease_owner = NULL,
                    next_attempt_at = LEAST(next_attempt_at, %s),
                    last_error = 'process_draining', updated_at = NOW()
                WHERE status = 'processing' AND lease_owner = %s
                """,
                (current, _WORKER_ID),
            )
        return int(result.rowcount or 0)

    def _set_delivery_state(self, *args: Any, **kwargs: Any) -> None:
        super()._set_delivery_state(*args, **kwargs)
        reminder_id = str(args[0] if args else kwargs.get("reminder_id") or "")
        if self.backend_name == "postgresql" and reminder_id:
            with self.store.pool.connection() as connection:
                connection.execute(
                    "UPDATE hero_reminders SET lease_owner = NULL WHERE reminder_id = %s",
                    (reminder_id,),
                )


def _repository() -> ReminderRepository:
    global _REMINDER_REPOSITORY
    if _REMINDER_REPOSITORY is None or _REMINDER_REPOSITORY.store is not core.store:
        _REMINDER_REPOSITORY = ResilientReminderRepository(core.store)
    return _REMINDER_REPOSITORY


def _language(profile: dict[str, Any]) -> str:
    return composed.composed._preferred_language(profile) if hasattr(composed, "composed") else "de"


def _enabled(name: str, *, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).strip().casefold() in {"1", "true", "yes", "on"}


def _canary_phone_hashes() -> set[str]:
    """Hash exact full-number Canary entries without logging the configured values."""
    hashes: set[str] = set()
    for raw in os.getenv("REMINDER_CANARY_SENDERS", "").split(","):
        compact = "".join(character for character in raw.strip() if character.isdigit() or character == "+")
        digits = "".join(character for character in compact if character.isdigit())
        if len(digits) < 8 or compact.count("+") > 1 or "+" in compact[1:]:
            continue
        # Meta sender IDs normally contain digits only, while operator input may
        # include a leading plus. Both representations identify the same exact
        # full number and no partial matching is used.
        hashes.add(reminder_recipient_hash(digits))
        hashes.add(reminder_recipient_hash(f"+{digits}"))
    return hashes


def _worker_configuration_status(store: Any | None = None) -> str:
    """Return a bounded, privacy-safe delivery prerequisite status."""
    active_store = core.store if store is None else store
    if not _enabled("REMINDER_WORKER_ENABLED"):
        return "disabled"
    if not reminder_encryption_ready():
        return "encryption_unavailable"
    if str(getattr(active_store, "backend_name", "json")) != "postgresql":
        return "storage_unavailable"
    if not all(os.getenv(name, "").strip() for name in ("WHATSAPP_TOKEN", "PHONE_NUMBER_ID")):
        return "outbound_unavailable"
    if not _canary_phone_hashes():
        return "canary_unavailable"
    return "configured"


def reminder_worker_status(store: Any | None = None) -> str:
    """Report whether delivery is configured and the background task is alive."""
    configuration = _worker_configuration_status(store)
    if configuration != "configured":
        return configuration
    if _WORKER_TASK is None or _WORKER_TASK.done():
        return "stopped"
    return "running"


def reminder_delivery_ready(sender: str | None = None) -> bool:
    """Fail closed unless the production delivery worker is currently alive."""
    if reminder_worker_status() != "running":
        return False
    return sender is None or reminder_recipient_hash(sender) in _canary_phone_hashes()


def _command_text(text: str) -> str:
    value = str(text or "").translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا"}))
    value = re.sub(r"(قبلها|قبل الموعد|قبل المهلة)\s+بيومين\b", r"\1 2 ايام", value)
    value = re.sub(r"(قبلها|قبل الموعد|قبل المهلة)\s+بيوم\b", r"\1 1 يوم", value)
    return value


def _render_reminder(language: str, title: str) -> str:
    return _ORIGINAL_RENDER_REMINDER(language, title).replace("offene متابعة", "offene Aufgabe")


def reminder_unavailable_message(language: str) -> str:
    return {
        "ar": "التذكيرات متوقفة مؤقتًا لحماية بياناتك. باقي خدمات سام شغّالة، وما حفظت هالتذكير.",
        "de": "Erinnerungen sind zum Schutz deiner Daten vorübergehend deaktiviert. Die übrigen Funktionen bleiben verfügbar; diese Erinnerung wurde nicht gespeichert.",
        "en": "Reminders are temporarily disabled to protect your data. Other features remain available, and this reminder was not saved.",
        "uk": "Нагадування тимчасово вимкнено для захисту твоїх даних. Інші функції працюють; це нагадування не збережено.",
        "el": "Οι υπενθυμίσεις είναι προσωρινά απενεργοποιημένες για την προστασία των δεδομένων σου. Οι άλλες λειτουργίες παραμένουν διαθέσιμες και αυτή η υπενθύμιση δεν αποθηκεύτηκε.",
    }.get(language, "Reminders are temporarily unavailable and this reminder was not saved.")


def mission_created_message(language: str, mission: dict[str, Any]) -> str:
    # This callback has no sender identity, so it cannot prove Canary
    # eligibility. Do not advertise reminder creation at this boundary.
    return _ORIGINAL_MISSION_CREATED_MESSAGE(language, mission)


async def process_incoming(message: core.IncomingMessage) -> None:
    profile = core.store.get_user(message.sender)
    language = _language(profile)
    stage = str(profile.get("onboarding_stage") or "")
    intent = detect_reminder_intent(_command_text(message.text)) if message.message_type == "text" else None
    if intent is None or stage != "complete":
        await _ORIGINAL_PROCESS_INCOMING(message)
        return
    memory_enabled = profile.get("memory_consent") == "granted"
    if not memory_enabled:
        await core._finish(message.message_id, core.memory_required_message(language), message.sender)
        return
    core.store.update_user(message.sender, {
        "last_seen": core._now().isoformat(),
        "session_language": language,
        "session_topic": "reminders",
        "session_expires_at": core._session_expiry(),
    })
    repository = _repository()
    if intent.action == "list":
        await core._finish(message.message_id, reminder_list_message(language, repository.list(message.sender, active_only=True, limit=10)), message.sender)
        return
    if intent.action in {"cancel", "cancel_all"}:
        count = repository.cancel(message.sender, all_active=intent.action == "cancel_all")
        await core._finish(message.message_id, reminder_cancelled_message(language, count), message.sender)
        return
    if not reminder_delivery_ready(message.sender):
        await core._finish(message.message_id, reminder_unavailable_message(language), message.sender)
        return
    mission = core._hero_memory().get_latest_mission(message.sender)
    scheduled_at = resolve_reminder_schedule(intent, mission)
    if scheduled_at is None:
        await core._finish(message.message_id, reminder_needs_date_message(language), message.sender)
        return
    title = str((mission or {}).get("title") or profile.get("current_topic") or "Follow-up")
    reminder = repository.create(
        message.sender,
        title=title,
        scheduled_at=scheduled_at,
        language=language,
        mission_id=str((mission or {}).get("mission_id") or ""),
    )
    await core._finish(message.message_id, reminder_created_message(language, reminder), message.sender)


async def _start_worker() -> None:
    global _WORKER_TASK, _WORKER_STOP
    configuration = _worker_configuration_status()
    if configuration != "configured":
        logger.info("Reminder worker not started: %s", configuration)
        return
    if _WORKER_TASK and not _WORKER_TASK.done():
        return
    _WORKER_STOP = asyncio.Event()
    _WORKER_TASK = asyncio.create_task(
        reminder_worker_loop(
            _repository(),
            core.store,
            send_text=core.send_whatsapp_message,
            send_template=send_whatsapp_template,
            stop_event=_WORKER_STOP,
            interval_seconds=int(os.getenv("REMINDER_POLL_SECONDS", "60")),
            template_name=os.getenv("WHATSAPP_REMINDER_TEMPLATE", "").strip(),
        ),
        name="amthero24-reminder-worker",
    )
    logger.info("Reminder worker started")


async def _stop_worker() -> None:
    global _WORKER_TASK, _WORKER_STOP
    if _WORKER_STOP:
        _WORKER_STOP.set()
    if _WORKER_TASK:
        try:
            await asyncio.wait_for(_WORKER_TASK, timeout=5)
        except (TimeoutError, asyncio.CancelledError):
            _WORKER_TASK.cancel()
    try:
        repository = _repository()
        if isinstance(repository, ResilientReminderRepository):
            repository.release_owned()
    finally:
        _WORKER_TASK = None
        _WORKER_STOP = None
        logger.info("Reminder worker stopped")


reminder_module.render_reminder = _render_reminder
composed.process_incoming = process_incoming
core.process_incoming = process_incoming
core.mission_created_message = mission_created_message
core.app.router.add_event_handler("startup", _start_worker)
core.app.router.add_event_handler("shutdown", _stop_worker)

app = composed.app
store = composed.store
