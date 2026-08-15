"""Runtime composition for grounded Refund, appointment, and contract drafts.

The wrapper is installed after the shared copy-safe and cancellation layers. It does
not call an additional provider or execute any external action. It validates a private
draft envelope against strong facts from the active request, replaces untrusted model
explanations with deterministic summaries, and serves deterministic option-2 help.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from draft_assistance import ASSISTANCE_EXPLAIN, detect_draft_assistance_action
from mvp_draft_grounding import (
    OfficialDraftJourney,
    build_grounding_failure_message,
    build_journey_companion_summary,
    build_journey_plain_explanation,
    classify_official_draft_journey,
    ground_official_journey_draft,
)
from official_draft_delivery import (
    DRAFT_MARKER,
    END_MARKER,
    EXPLANATION_MARKER,
    is_official_draft_turn,
    looks_like_official_draft,
    parse_copy_safe_draft_reply,
)

logger = logging.getLogger("amthero24.mvp_draft_grounding")
_CORE_MARKER = "_mvp_draft_grounding_installed"
_HELPER_MARKER = "_mvp_draft_grounding_helpers_patched"


@dataclass
class _JourneyDeliveryState:
    sender: str
    message_id: str
    language: str
    request_text: str
    previous_draft: str
    journey: OfficialDraftJourney
    before_profile: dict[str, Any]
    rejected: bool = False
    fallback_text: str = ""


_ACTIVE_JOURNEY_DELIVERY: ContextVar[_JourneyDeliveryState | None] = ContextVar(
    "amthero24_active_mvp_draft_grounding",
    default=None,
)


def _conversation_language(core: Any, message: Any, profile: dict[str, Any]) -> str:
    memory_enabled = profile.get("memory_consent") == "granted"
    fallback = str(
        profile.get("preferred_language") if memory_enabled else profile.get("session_language")
        or profile.get("preferred_language")
        or "de"
    )
    text = str(getattr(message, "text", "") or "")
    language = core.detect_language(text, fallback) if text.strip() else fallback
    return language if language in {"de", "ar", "en", "uk", "el"} else "de"


def _session_expiry(core: Any, profile: dict[str, Any]) -> str:
    builder = getattr(core, "_session_expiry", None)
    if callable(builder):
        try:
            return str(builder())
        except Exception:
            logger.warning("Unable to refresh grounded-draft session expiry")
    return str(profile.get("session_expires_at") or "")


def _retain_clean_draft_context(
    core: Any,
    sender: str,
    profile: dict[str, Any],
    draft: str,
) -> None:
    updates: dict[str, Any] = {"session_last_reply": draft}
    expiry = _session_expiry(core, profile)
    if expiry:
        updates["session_expires_at"] = expiry
    if (
        profile.get("memory_consent") == "granted"
        and str(profile.get("last_assistant_reply") or "") == draft
    ):
        updates["last_assistant_reply"] = draft
    core.store.update_user(sender, updates)


def _restore_after_rejection(core: Any, state: _JourneyDeliveryState) -> None:
    """Remove the rejected model payload from conversation/reusable reply context."""
    before = state.before_profile
    memory_enabled = before.get("memory_consent") == "granted"
    retained = state.previous_draft if looks_like_official_draft(state.previous_draft) else state.fallback_text
    updates: dict[str, Any] = {"session_last_reply": retained}
    expiry = _session_expiry(core, before)
    if expiry:
        updates["session_expires_at"] = expiry

    if memory_enabled:
        if state.previous_draft and str(before.get("last_assistant_reply") or "") == state.previous_draft:
            updates["last_assistant_reply"] = state.previous_draft
        elif "last_assistant_reply" in before:
            updates["last_assistant_reply"] = before["last_assistant_reply"]
        else:
            core.store.remove_user_fields(state.sender, {"last_assistant_reply"})
    core.store.update_user(state.sender, updates)


def _patch_shared_helpers(official_runtime: Any) -> None:
    if getattr(official_runtime, _HELPER_MARKER, False):
        return
    original_card = official_runtime.build_draft_assistance_card

    def grounded_card(
        draft: str,
        explanation: str,
        *,
        conversation_language: str,
    ) -> str:
        journey = classify_official_draft_journey("", previous_draft=draft)
        if journey is not None:
            summary = build_journey_companion_summary(
                draft,
                journey=journey,
                conversation_language=conversation_language,
            )
            if summary is not None:
                return original_card(
                    draft,
                    summary,
                    conversation_language=conversation_language,
                )
        return original_card(
            draft,
            explanation,
            conversation_language=conversation_language,
        )

    official_runtime.build_draft_assistance_card = grounded_card
    setattr(official_runtime, _HELPER_MARKER, True)


def install(core: Any, official_runtime: Any) -> None:
    """Install one idempotent post-generation grounding wrapper."""
    if getattr(core, _CORE_MARKER, False):
        return

    _patch_shared_helpers(official_runtime)
    original_process = core.process_incoming
    original_send = core.send_whatsapp_message

    async def send_whatsapp_message(recipient: str, text: str) -> None:
        state = _ACTIVE_JOURNEY_DELIVERY.get()
        if state is not None and recipient == state.sender:
            parsed = parse_copy_safe_draft_reply(
                text,
                conversation_language=state.language,
            )
            if parsed is not None:
                grounded = ground_official_journey_draft(
                    state.request_text,
                    parsed.draft,
                    previous_draft=state.previous_draft,
                    conversation_language=state.language,
                )
                if grounded.applicable:
                    if grounded.rejection_reason:
                        state.rejected = True
                        state.fallback_text = build_grounding_failure_message(
                            journey=state.journey,
                            conversation_language=state.language,
                        )
                        logger.warning(
                            "Official journey draft rejected safely",
                            extra={
                                "message_id": state.message_id,
                                "journey": state.journey.value,
                                "rejection_reason": grounded.rejection_reason,
                            },
                        )
                        await original_send(recipient, state.fallback_text)
                        return
                    explanation = build_journey_companion_summary(
                        grounded.draft,
                        journey=state.journey,
                        conversation_language=parsed.conversation_language,
                    ) or parsed.explanation
                    envelope = (
                        f"{DRAFT_MARKER}\n{grounded.draft}\n"
                        f"{EXPLANATION_MARKER}\n{explanation}\n{END_MARKER}"
                    )
                    await original_send(recipient, envelope)
                    return
        await original_send(recipient, text)

    async def process_incoming(message: Any) -> None:
        profile = core.store.get_user(message.sender)
        stage = str(profile.get("onboarding_stage") or "")
        text = str(getattr(message, "text", "") or "")
        commands_allowed = getattr(message, "internal_context", "") != "document_analysis"
        supported_text_type = getattr(message, "message_type", "") in {"text", "button", "interactive"}
        previous_draft = str(
            profile.get("session_last_reply")
            or profile.get("last_assistant_reply")
            or ""
        )

        assistance_action = (
            detect_draft_assistance_action(text, previous_draft)
            if commands_allowed
            and supported_text_type
            and stage == "complete"
            and text.strip()
            else None
        )
        previous_journey = classify_official_draft_journey("", previous_draft=previous_draft)
        if assistance_action == ASSISTANCE_EXPLAIN and previous_journey is not None:
            language = _conversation_language(core, message, profile)
            explanation = build_journey_plain_explanation(
                previous_draft,
                journey=previous_journey,
                conversation_language=language,
            )
            if explanation is not None:
                await original_send(message.sender, explanation)
                core.store.update_message_status(message.message_id, "sent")
                _retain_clean_draft_context(
                    core,
                    message.sender,
                    profile,
                    previous_draft,
                )
                return

        journey = classify_official_draft_journey(text, previous_draft=previous_draft)
        active = bool(
            commands_allowed
            and getattr(message, "message_type", "") == "text"
            and stage == "complete"
            and text.strip()
            and journey is not None
            and is_official_draft_turn(text, profile)
        )
        if not active or journey is None:
            await original_process(message)
            return

        state = _JourneyDeliveryState(
            sender=message.sender,
            message_id=message.message_id,
            language=_conversation_language(core, message, profile),
            request_text=text,
            previous_draft=previous_draft if looks_like_official_draft(previous_draft) else "",
            journey=journey,
            before_profile=dict(profile),
        )
        token = _ACTIVE_JOURNEY_DELIVERY.set(state)
        try:
            await original_process(message)
        finally:
            _ACTIVE_JOURNEY_DELIVERY.reset(token)

        if state.rejected:
            _restore_after_rejection(core, state)

    core.send_whatsapp_message = send_whatsapp_message
    core.process_incoming = process_incoming
    setattr(core, _CORE_MARKER, True)
