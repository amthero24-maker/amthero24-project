"""Follow-up and reminder composition layer for the AmtHero24 production app."""
from __future__ import annotations

import asyncio
import os
import re
from datetime import UTC, datetime
from typing import Any

import document_extensions as composed
import reminder_engine as reminder_module
from reminder_engine import (
    ReminderRepository,
    detect_reminder_intent,
    reminder_cancelled_message,
    reminder_created_message,
    reminder_list_message,
    reminder_needs_date_message,
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


class ResilientReminderRepository(ReminderRepository):
    """Reclaim reminder leases left behind by a stopped Railway process."""

    def claim_due(self, *, now: datetime | None = None, limit: int = 10) -> list[dict[str, Any]]:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        if self.backend_name == "postgresql":
            with self.store.pool.connection() as connection:
                connection.execute(
                    """
                    UPDATE hero_reminders
                    SET status = 'failed', lease_until = NULL, next_attempt_at = LEAST(next_attempt_at, %s),
                        last_error = 'expired_delivery_lease', updated_at = NOW()
                    WHERE status = 'processing' AND lease_until IS NOT NULL AND lease_until < %s
                    """,
                    (current, current),
                )
        else:
            def reclaim(data: dict[str, Any]) -> None:
                for item in data.setdefault("reminders", {}).values():
                    if not isinstance(item, dict) or item.get("status") != "processing" or not item.get("lease_until"):
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
        return super().claim_due(now=current, limit=limit)


def _repository() -> ReminderRepository:
    global _REMINDER_REPOSITORY
    if _REMINDER_REPOSITORY is None or _REMINDER_REPOSITORY.store is not core.store:
        _REMINDER_REPOSITORY = ResilientReminderRepository(core.store)
    return _REMINDER_REPOSITORY


def _language(profile: dict[str, Any]) -> str:
    return composed.composed._preferred_language(profile) if hasattr(composed, "composed") else "de"


def _command_text(text: str) -> str:
    """Normalize common Arabic spelling variants before deterministic parsing."""
    value = str(text or "").translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا"}))
    value = re.sub(r"(قبلها|قبل الموعد|قبل المهلة)\s+بيومين\b", r"\1 2 ايام", value)
    value = re.sub(r"(قبلها|قبل الموعد|قبل المهلة)\s+بيوم\b", r"\1 1 يوم", value)
    return value


def _render_reminder(language: str, title: str) -> str:
    """Keep every reminder entirely in the user's selected language."""
    return _ORIGINAL_RENDER_REMINDER(language, title).replace("offene متابعة", "offene Aufgabe")


def _reminder_prompt(language: str) -> str:
    return {
        "ar": "إذا بتحب، اكتب «ذكرني قبلها بيوم» وبسجّل التذكير بإذنك.",
        "de": "Wenn du möchtest, schreib „Erinnere mich einen Tag vorher“, dann speichere ich die Erinnerung.",
        "en": "You can say “remind me one day before” and I will save the reminder with your permission.",
        "uk": "За бажанням напиши «нагадай за один день», і я збережу нагадування.",
        "el": "Αν θέλεις, γράψε «θύμισέ μου μία ημέρα πριν» και θα αποθηκεύσω την υπενθύμιση.",
    }.get(language, "Say ‘remind me one day before’ to save a reminder.")


def mission_created_message(language: str, mission: dict[str, Any]) -> str:
    reply = _ORIGINAL_MISSION_CREATED_MESSAGE(language, mission)
    if str(mission.get("_operation") or "") == "due" and mission.get("due_at"):
        return reply + "\n\n" + _reminder_prompt(language)
    return reply


async def process_incoming(message: core.IncomingMessage) -> None:
    profile = core.store.get_user(message.sender)
    language = _language(profile)
    stage = str(profile.get("onboarding_stage") or "")
    intent = detect_reminder_intent(_command_text(message.text)) if message.message_type == "text" else None

    # Let onboarding and consent handling run before long-term reminder commands.
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
        await core._finish(
            message.message_id,
            reminder_list_message(language, repository.list(message.sender, active_only=True, limit=10)),
            message.sender,
        )
        return

    if intent.action in {"cancel", "cancel_all"}:
        count = repository.cancel(message.sender, all_active=intent.action == "cancel_all")
        await core._finish(message.message_id, reminder_cancelled_message(language, count), message.sender)
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
    enabled = os.getenv("REMINDER_WORKER_ENABLED", "true").strip().casefold() not in {"0", "false", "no", "off"}
    if not enabled or str(getattr(core.store, "backend_name", "json")) != "postgresql":
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


async def _stop_worker() -> None:
    global _WORKER_TASK, _WORKER_STOP
    if _WORKER_STOP:
        _WORKER_STOP.set()
    if _WORKER_TASK:
        try:
            await asyncio.wait_for(_WORKER_TASK, timeout=5)
        except (TimeoutError, asyncio.CancelledError):
            _WORKER_TASK.cancel()
    _WORKER_TASK = None
    _WORKER_STOP = None


# Replace request-time targets after all lower composition layers are loaded.
reminder_module.render_reminder = _render_reminder
composed.process_incoming = process_incoming
core.process_incoming = process_incoming
core.mission_created_message = mission_created_message
core.app.router.add_event_handler("startup", _start_worker)
core.app.router.add_event_handler("shutdown", _stop_worker)

app = composed.app
store = composed.store
