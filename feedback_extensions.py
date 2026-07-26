"""Production composition for anonymous Beta quality feedback."""
from __future__ import annotations

import os
from typing import Any

import admin_extensions as admin_module
import privacy_engine as privacy_module
import support_extensions as composed
from feedback_engine import FeedbackRepository, detect_feedback, feedback_ack

core = composed.core
_ORIGINAL_PROCESS_INCOMING = core.process_incoming
_ORIGINAL_ADMIN_BUILD_OVERVIEW = admin_module.build_overview
_ORIGINAL_PRIVACY_CLEANUP = privacy_module.cleanup_retention
_FEEDBACK_REPOSITORY: FeedbackRepository | None = None


def _repository(store: Any | None = None) -> FeedbackRepository:
    global _FEEDBACK_REPOSITORY
    target = store or core.store
    if _FEEDBACK_REPOSITORY is None or _FEEDBACK_REPOSITORY.store is not target:
        _FEEDBACK_REPOSITORY = FeedbackRepository(target)
    return _FEEDBACK_REPOSITORY


def _language(profile: dict[str, Any]) -> str:
    language = str(
        profile.get("preferred_language")
        if profile.get("memory_consent") == "granted"
        else profile.get("session_language") or profile.get("preferred_language") or "de"
    )
    return language if language in {"de", "ar", "en", "uk", "el"} else "de"


async def process_incoming(message: core.IncomingMessage) -> None:
    score = detect_feedback(message.text) if message.message_type == "text" else None
    if score is None:
        await _ORIGINAL_PROCESS_INCOMING(message)
        return

    profile = core.store.get_user(message.sender)
    previous_language = _language(profile)
    language = core.detect_language(message.text, previous_language) if message.text.strip() else previous_language
    topic = str(
        profile.get("current_topic")
        if profile.get("memory_consent") == "granted"
        else profile.get("session_topic") or profile.get("current_topic") or "general"
    )
    _repository().record(score, language=language, topic=topic)
    await core._finish(message.message_id, feedback_ack(language, score), message.sender)


def _build_overview(store: Any, **kwargs: Any) -> dict[str, Any]:
    payload = _ORIGINAL_ADMIN_BUILD_OVERVIEW(store, **kwargs)
    payload["quality_feedback"] = _repository(store).aggregate(days=30)
    return payload


def _privacy_cleanup(store: Any, **kwargs: Any) -> dict[str, int]:
    result = _ORIGINAL_PRIVACY_CLEANUP(store, **kwargs)
    result["anonymous_feedback"] = _repository(store).cleanup(
        days=int(os.getenv("FEEDBACK_RETENTION_DAYS", "365"))
    )
    return result


_repository()
admin_module.build_overview = _build_overview
privacy_module.cleanup_retention = _privacy_cleanup
core.process_incoming = process_incoming

app = composed.app
store = composed.store
