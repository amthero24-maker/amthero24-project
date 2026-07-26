"""Top-level composition for privacy-safe burst and abuse protection."""
from __future__ import annotations

import os
import re
from typing import Any

import admin_extensions as admin_module
import entitlement_extensions as composed
import privacy_engine as privacy_module
import privacy_extensions as privacy_composed
from abuse_guard import AbuseGuardRepository, blocked_message, enforcement_enabled, guard_enabled

core = composed.core
_ORIGINAL_PROCESS_INCOMING = core.process_incoming
_ORIGINAL_PRIVACY_DELETE = privacy_composed.delete_all_user_data
_ORIGINAL_PRIVACY_CLEANUP = privacy_module.cleanup_retention
_ORIGINAL_ADMIN_BUILD_OVERVIEW = admin_module.build_overview
_ABUSE_REPOSITORY: AbuseGuardRepository | None = None


def _repository(store: Any | None = None) -> AbuseGuardRepository:
    global _ABUSE_REPOSITORY
    target = store or core.store
    if _ABUSE_REPOSITORY is None or _ABUSE_REPOSITORY.store is not target:
        _ABUSE_REPOSITORY = AbuseGuardRepository(target)
    return _ABUSE_REPOSITORY


def _normalize(text: str) -> str:
    value = str(text or "").casefold().strip()
    value = value.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا"}))
    value = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", value)
    value = re.sub(r"[؟،؛!?.,:;]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


_ESSENTIAL_PRIVACY_COMMANDS = (
    "امسح بياناتي", "احذف بياناتي", "صدر بياناتي", "نزل بياناتي", "وقف كل التذكيرات",
    "lösch meine daten", "daten löschen", "meine daten exportieren", "alle erinnerungen stoppen",
    "delete my data", "export my data", "cancel all reminders", "stop all reminders",
    "видали мої дані", "експортуй мої дані", "скасуй усі нагадування",
    "διαγραψε τα δεδομενα μου", "εξαγωγη δεδομενων", "ακυρωσε ολες τις υπενθυμισεις",
)


def _essential_command(message: core.IncomingMessage) -> bool:
    if message.message_type != "text":
        return False
    normalized = _normalize(message.text)
    return any(_normalize(command) in normalized for command in _ESSENTIAL_PRIVACY_COMMANDS)


def _language(profile: dict[str, Any]) -> str:
    language = str(
        profile.get("preferred_language")
        if profile.get("memory_consent") == "granted"
        else profile.get("session_language") or profile.get("preferred_language") or "de"
    )
    return language if language in {"de", "ar", "en", "uk", "el"} else "de"


async def process_incoming(message: core.IncomingMessage) -> None:
    if _essential_command(message):
        await _ORIGINAL_PROCESS_INCOMING(message)
        return

    decision = _repository().check(message.sender, has_media=message.media_id is not None)
    if not decision.allowed:
        if decision.notify:
            language = _language(core.store.get_user(message.sender))
            await core._finish(message.message_id, blocked_message(language, decision.blocked_until), message.sender)
        else:
            core.store.update_message_status(message.message_id, "rate_limited")
        return

    await _ORIGINAL_PROCESS_INCOMING(message)


def _delete_all_user_data(store: Any, phone: str) -> bool:
    guard_deleted = _repository(store).delete_user(phone)
    return bool(_ORIGINAL_PRIVACY_DELETE(store, phone) or guard_deleted)


def _cleanup_retention(store: Any, **kwargs: Any) -> dict[str, int]:
    result = _ORIGINAL_PRIVACY_CLEANUP(store, **kwargs)
    cleanup = _repository(store).cleanup(
        now=kwargs.get("now"),
        retention_days=int(os.getenv("ABUSE_EVENT_RETENTION_DAYS", "30")),
    )
    result["abuse_windows"] = cleanup["windows"]
    result["abuse_blocks"] = cleanup["blocks"]
    result["abuse_events"] = cleanup["events"]
    return result


def _build_overview(store: Any, **kwargs: Any) -> dict[str, Any]:
    payload = _ORIGINAL_ADMIN_BUILD_OVERVIEW(store, **kwargs)
    payload["abuse_guard"] = _repository(store).aggregate(now=kwargs.get("now"))
    return payload


_repository()
privacy_composed.delete_all_user_data = _delete_all_user_data
privacy_module.cleanup_retention = _cleanup_retention
admin_module.build_overview = _build_overview
core.process_incoming = process_incoming

app = composed.app
store = composed.store
ABUSE_GUARD_STATUS = "disabled" if not guard_enabled() else ("enforced" if enforcement_enabled() else "observe-only")
