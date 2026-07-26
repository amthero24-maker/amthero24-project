"""Subscription-ready entitlement composition without activating payments."""
from __future__ import annotations

import re
from typing import Any

import admin_extensions as composed
import privacy_extensions as privacy_composed
from entitlement_engine import EntitlementRepository, limit_reached_message, plan_summary_message
from hero_memory import HeroMemory

core = composed.core
_ORIGINAL_PROCESS_INCOMING = core.process_incoming
_ORIGINAL_EXPORT_USER_DATA = HeroMemory.export_user_data
_ORIGINAL_EXPORT_REPLY = core._export_reply
_ORIGINAL_PRIVACY_DELETE = privacy_composed.delete_all_user_data
_ENTITLEMENT_REPOSITORY: EntitlementRepository | None = None


def _repository(store: Any | None = None) -> EntitlementRepository:
    global _ENTITLEMENT_REPOSITORY
    target = store or core.store
    if _ENTITLEMENT_REPOSITORY is None or _ENTITLEMENT_REPOSITORY.store is not target:
        _ENTITLEMENT_REPOSITORY = EntitlementRepository(target)
    return _ENTITLEMENT_REPOSITORY


def _normalize(text: str) -> str:
    value = str(text or "").casefold().strip()
    value = value.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا"}))
    value = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", value)
    value = re.sub(r"[؟،؛!?.,:;]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


_PLAN_REQUESTS = {
    "شو خطتي", "ما هي خطتي", "شو اشتراكي", "حسابي", "استخدامي", "كم ضل عندي",
    "mein plan", "mein zugang", "meine nutzung", "welchen plan habe ich",
    "my plan", "my access", "my usage", "what plan am i on",
    "мій план", "моє використання", "το πλανο μου", "η χρηση μου",
}


def is_plan_request(text: str) -> bool:
    normalized = _normalize(text)
    return normalized in {_normalize(item) for item in _PLAN_REQUESTS}


def _language(profile: dict[str, Any]) -> str:
    language = str(
        profile.get("preferred_language")
        if profile.get("memory_consent") == "granted"
        else profile.get("session_language") or profile.get("preferred_language") or "de"
    )
    return language if language in {"de", "ar", "en", "uk", "el"} else "de"


def _metric(message: core.IncomingMessage) -> str:
    if message.message_type == "image":
        return "images_monthly"
    if message.message_type == "document":
        return "documents_monthly"
    if message.message_type == "audio":
        return "voice_monthly"
    return "messages_daily"


def _safe_export_reply(language: str, entitlement: dict[str, Any]) -> str:
    plan = str(entitlement.get("plan") or "beta")
    status = str(entitlement.get("status") or "active")
    return {
        "ar": f"\n\nالوصول والخطة:\n• الخطة: {plan}\n• الحالة: {status}",
        "de": f"\n\nZugang und Plan:\n• Plan: {plan}\n• Status: {status}",
        "en": f"\n\nAccess and plan:\n• Plan: {plan}\n• Status: {status}",
        "uk": f"\n\nДоступ і план:\n• План: {plan}\n• Статус: {status}",
        "el": f"\n\nΠρόσβαση και πλάνο:\n• Πλάνο: {plan}\n• Κατάσταση: {status}",
    }.get(language, f"\n\nAccess and plan:\n• Plan: {plan}\n• Status: {status}")


async def process_incoming(message: core.IncomingMessage) -> None:
    profile = core.store.get_user(message.sender)
    language = _language(profile)

    if message.message_type == "text" and is_plan_request(message.text):
        summary = _repository().summary(message.sender)
        await core._finish(message.message_id, plan_summary_message(language, summary), message.sender)
        return

    decision = _repository().check_and_consume(message.sender, _metric(message))
    if not decision.allowed:
        await core._finish(message.message_id, limit_reached_message(language, decision.metric), message.sender)
        return

    await _ORIGINAL_PROCESS_INCOMING(message)


def _export_user_data(self: HeroMemory, phone: str) -> dict[str, Any]:
    payload = _ORIGINAL_EXPORT_USER_DATA(self, phone)
    payload["entitlement"] = _repository(self.store).export_user(phone)
    return payload


def _export_reply(language: str, payload: dict[str, Any]) -> str:
    base = _ORIGINAL_EXPORT_REPLY(language, payload)
    entitlement = payload.get("entitlement") if isinstance(payload.get("entitlement"), dict) else None
    return base + (_safe_export_reply(language, entitlement) if entitlement else "")


def _delete_all_user_data(store: Any, phone: str) -> bool:
    entitlement_deleted = _repository(store).delete_user(phone)
    return bool(_ORIGINAL_PRIVACY_DELETE(store, phone) or entitlement_deleted)


_repository()
HeroMemory.export_user_data = _export_user_data
core._export_reply = _export_reply
privacy_composed.delete_all_user_data = _delete_all_user_data
core.process_incoming = process_incoming

app = composed.app
store = composed.store
