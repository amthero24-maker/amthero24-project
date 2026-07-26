"""Follow-up and reminder composition layer for the AmtHero24 production app."""
from __future__ import annotations

import asyncio
import os
from typing import Any

import document_extensions as composed
from reminder_engine import (
    ReminderRepository,
    deliver_due_reminders,
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
_REMINDER_REPOSITORY: ReminderRepository | None = None
_WORKER_TASK: asyncio.Task[None] | None = None
_WORKER_STOP: asyncio.Event | None = None


def _repository() -> ReminderRepository:
    global _REMINDER_REPOSITORY
    if _REMINDER_REPOSITORY is None or _REMINDER_REPOSITORY.store is not core.store:
        _REMINDER_REPOSITORY = ReminderRepository(core.store)
    return _REMINDER_REPOSITORY


def _language(profile: dict[str, Any]) -> str:
    return composed.composed._preferred_language(profile) if hasattr(composed, "composed") else "de"


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
    intent = detect_reminder_intent(message.text) if message.message_type == "text" else None

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
composed.process_incoming = process_incoming
core.process_incoming = process_incoming
core.mission_created_message = mission_created_message
core.app.add_event_handler("startup", _start_worker)
core.app.add_event_handler("shutdown", _stop_worker)

app = composed.app
store = composed.store
