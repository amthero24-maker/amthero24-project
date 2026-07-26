"""Production privacy-control composition for AmtHero24."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Any

import reminder_extensions as composed
from hero_memory import HeroMemory
from privacy_engine import delete_all_user_data, export_user_data, retention_worker_loop
from reminder_engine import ReminderRepository

logger = logging.getLogger("amthero24.privacy")
core = composed.core
_ORIGINAL_EXPORT_USER_DATA = HeroMemory.export_user_data
_ORIGINAL_EXPORT_REPLY = core._export_reply
_RETENTION_TASK: asyncio.Task[None] | None = None
_RETENTION_STOP: asyncio.Event | None = None


def _reminder_repository(store: Any) -> ReminderRepository:
    current = composed._REMINDER_REPOSITORY
    if current is not None and current.store is store:
        return current
    return composed.ResilientReminderRepository(store)


def _export_user_data(self: HeroMemory, phone: str) -> dict[str, Any]:
    base = _ORIGINAL_EXPORT_USER_DATA(self, phone)
    return export_user_data(self.store, phone, base, _reminder_repository(self.store))


def _delete_all_user_data(self: HeroMemory, phone: str) -> bool:
    return delete_all_user_data(self.store, phone)


def _export_reply(language: str, payload: dict[str, Any]) -> str:
    base = _ORIGINAL_EXPORT_REPLY(language, payload)
    reminders = payload.get("reminders", []) if isinstance(payload.get("reminders"), list) else []
    lines = [
        f"• {item.get('title')} — {item.get('scheduled_at')} — {item.get('status')}"
        for item in reminders
        if isinstance(item, dict) and item.get("title")
    ]
    if not lines:
        return base
    heading = {
        "ar": "\n\nتذكيراتك:",
        "de": "\n\nDeine Erinnerungen:",
        "en": "\n\nYour reminders:",
        "uk": "\n\nТвої нагадування:",
        "el": "\n\nΟι υπενθυμίσεις σου:",
    }.get(language, "\n\nYour reminders:")
    return base + heading + "\n" + "\n".join(lines)


def _deletion_confirmation(language: str) -> str:
    return {
        "ar": "تم حذف بياناتك الشخصية ورسائلك المحفوظة ومهامك وتذكيراتك بالكامل ✅ فيك ترجع تستخدم سام بأي وقت من دون ذاكرة سابقة.",
        "de": "Deine personenbezogenen Daten, gespeicherten Nachrichten, Aufgaben und Erinnerungen wurden vollständig gelöscht ✅",
        "en": "Your personal data, saved messages, tasks, and reminders have been fully deleted ✅",
        "uk": "Твої персональні дані, збережені повідомлення, завдання та нагадування повністю видалено ✅",
        "el": "Τα προσωπικά δεδομένα, τα αποθηκευμένα μηνύματα, οι εργασίες και οι υπενθυμίσεις σου διαγράφηκαν πλήρως ✅",
    }.get(language, "Your personal data has been fully deleted.")


async def _retention_error(exc: Exception) -> None:
    logger.exception("Privacy retention iteration failed", exc_info=exc)


async def _start_retention_worker() -> None:
    global _RETENTION_TASK, _RETENTION_STOP
    enabled = os.getenv("PRIVACY_RETENTION_ENABLED", "true").strip().casefold() not in {"0", "false", "no", "off"}
    if not enabled or (_RETENTION_TASK and not _RETENTION_TASK.done()):
        return
    _RETENTION_STOP = asyncio.Event()
    _RETENTION_TASK = asyncio.create_task(
        retention_worker_loop(
            core.store,
            stop_event=_RETENTION_STOP,
            interval_seconds=int(os.getenv("PRIVACY_RETENTION_INTERVAL_SECONDS", "21600")),
            on_error=_retention_error,
        ),
        name="amthero24-privacy-retention-worker",
    )


async def _stop_retention_worker() -> None:
    global _RETENTION_TASK, _RETENTION_STOP
    if _RETENTION_STOP:
        _RETENTION_STOP.set()
    if _RETENTION_TASK:
        try:
            await asyncio.wait_for(_RETENTION_TASK, timeout=5)
        except (TimeoutError, asyncio.CancelledError):
            _RETENTION_TASK.cancel()
    _RETENTION_TASK = None
    _RETENTION_STOP = None


HeroMemory.export_user_data = _export_user_data
HeroMemory.delete_all_user_data = _delete_all_user_data
core._export_reply = _export_reply
core._deletion_confirmation = _deletion_confirmation
core.app.router.add_event_handler("startup", _start_retention_worker)
core.app.router.add_event_handler("shutdown", _stop_retention_worker)

app = composed.app
store = composed.store
