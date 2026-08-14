"""Runtime composition for product-wide copy-safe official draft delivery.

The wrapper is installed inside the existing WhatsApp application boundary. It uses
context-local state, never changes provider/runtime flags, and only separates an
already-generated official draft from its explanation before delivery.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from official_draft_delivery import (
    DRAFT_OUTPUT_KIND,
    ORDINARY_OUTPUT_KIND,
    CopySafeDraftFormatError,
    CopySafeDraftReply,
    activate_official_draft_turn,
    draft_reply_requires_fail_closed,
    is_official_draft_turn,
    parse_copy_safe_draft_reply,
    reset_official_draft_turn,
)

logger = logging.getLogger("amthero24.official_draft_delivery")
_CORE_MARKER = "_official_draft_runtime_installed"


@dataclass
class _DeliveryState:
    sender: str
    message_id: str
    language: str
    parsed: CopySafeDraftReply | None = None


_ACTIVE_DELIVERY: ContextVar[_DeliveryState | None] = ContextVar(
    "amthero24_active_official_draft_delivery",
    default=None,
)


def _conversation_language(core: Any, message: Any, profile: dict[str, Any]) -> str:
    memory_enabled = profile.get("memory_consent") == "granted"
    fallback = str(
        profile.get("preferred_language") if memory_enabled else profile.get("session_language")
        or profile.get("preferred_language")
        or "de"
    )
    language = (
        core.detect_language(str(message.text or ""), fallback)
        if str(message.text or "").strip()
        else fallback
    )
    return language if language in {"de", "ar", "en", "uk", "el"} else "de"


def _update_output_context(
    core: Any,
    message: Any,
    profile: dict[str, Any],
    parsed: CopySafeDraftReply | None,
) -> None:
    updates: dict[str, Any] = {
        "session_output_kind": DRAFT_OUTPUT_KIND if parsed is not None else ORDINARY_OUTPUT_KIND,
    }
    if parsed is not None:
        updates["session_last_reply"] = parsed.draft
        if profile.get("memory_consent") == "granted":
            updates["last_assistant_reply"] = parsed.draft
    core.store.update_user(message.sender, updates)


def install(core: Any) -> None:
    """Install one context-safe wrapper around the current application path."""
    if getattr(core, _CORE_MARKER, False):
        return

    original_process = core.process_incoming
    original_send = core.send_whatsapp_message

    async def send_whatsapp_message(recipient: str, text: str) -> None:
        state = _ACTIVE_DELIVERY.get()
        if state is None or recipient != state.sender or state.parsed is not None:
            await original_send(recipient, text)
            return

        parsed = parse_copy_safe_draft_reply(
            text,
            conversation_language=state.language,
        )
        if parsed is None:
            if draft_reply_requires_fail_closed(text):
                logger.error(
                    "Official draft reply could not be separated safely",
                    extra={"message_id": state.message_id},
                )
                raise CopySafeDraftFormatError("official_draft_reply_ambiguous")
            await original_send(recipient, text)
            return

        state.parsed = parsed
        await original_send(recipient, parsed.draft)
        core.store.update_message_status(state.message_id, "sent")
        try:
            await original_send(recipient, parsed.explanation)
        except Exception:
            logger.warning(
                "Secondary official draft explanation delivery failed",
                extra={"message_id": state.message_id},
            )

    async def process_incoming(message: Any) -> None:
        profile = core.store.get_user(message.sender)
        stage = str(profile.get("onboarding_stage") or "")
        commands_allowed = getattr(message, "internal_context", "") != "document_analysis"
        active = bool(
            commands_allowed
            and getattr(message, "message_type", "") == "text"
            and stage == "complete"
            and str(message.text or "").strip()
            and is_official_draft_turn(str(message.text or ""), profile)
        )

        if not active:
            previous_kind = str(profile.get("session_output_kind") or "")
            await original_process(message)
            if previous_kind == DRAFT_OUTPUT_KIND:
                core.store.update_user(message.sender, {
                    "session_output_kind": ORDINARY_OUTPUT_KIND,
                })
            return

        state = _DeliveryState(
            sender=message.sender,
            message_id=message.message_id,
            language=_conversation_language(core, message, profile),
        )
        delivery_token = _ACTIVE_DELIVERY.set(state)
        draft_token = activate_official_draft_turn()
        try:
            await original_process(message)
        finally:
            reset_official_draft_turn(draft_token)
            _ACTIVE_DELIVERY.reset(delivery_token)

        _update_output_context(core, message, profile, state.parsed)

    core.send_whatsapp_message = send_whatsapp_message
    core.process_incoming = process_incoming
    setattr(core, _CORE_MARKER, True)
