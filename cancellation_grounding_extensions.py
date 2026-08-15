"""Runtime composition for grounded cancellation drafts and explanations.

The layer is installed after the shared copy-safe draft runtime. It does not call a
provider or execute a cancellation. It rewrites only a validated private draft
envelope before the shared runtime delivers it, and serves deterministic cancellation
explanation/field-help choices while preserving the clean draft as session context.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from cancellation_grounding_refinements import (
    build_cancellation_assistance_card,
    build_cancellation_companion_summary,
    build_cancellation_missing_fields_help,
    build_cancellation_plain_explanation,
    ground_cancellation_draft,
    is_cancellation_draft,
    is_cancellation_request,
)
from draft_assistance import (
    ASSISTANCE_EXPLAIN,
    detect_draft_assistance_action,
)
from official_draft_delivery import (
    DRAFT_MARKER,
    END_MARKER,
    EXPLANATION_MARKER,
    CopySafeDraftFormatError,
    is_official_draft_turn,
    parse_copy_safe_draft_reply,
)

logger = logging.getLogger("amthero24.cancellation_grounding")
_CORE_MARKER = "_cancellation_grounding_installed"
_HELPER_MARKER = "_cancellation_grounding_helpers_patched"


@dataclass(frozen=True)
class _CancellationDeliveryState:
    sender: str
    message_id: str
    language: str
    request_text: str
    previous_draft: str


_ACTIVE_CANCELLATION_DELIVERY: ContextVar[_CancellationDeliveryState | None] = ContextVar(
    "amthero24_active_cancellation_grounding",
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


def _retain_clean_draft_context(
    core: Any,
    sender: str,
    profile: dict[str, Any],
    draft: str,
) -> None:
    updates: dict[str, Any] = {"session_last_reply": draft}
    expiry_builder = getattr(core, "_session_expiry", None)
    if callable(expiry_builder):
        try:
            updates["session_expires_at"] = str(expiry_builder())
        except Exception:
            logger.warning("Unable to refresh cancellation-assistance session expiry")
    if (
        profile.get("memory_consent") == "granted"
        and str(profile.get("last_assistant_reply") or "") == draft
    ):
        updates["last_assistant_reply"] = draft
    core.store.update_user(sender, updates)


def _patch_shared_helpers(official_runtime: Any) -> None:
    if getattr(official_runtime, _HELPER_MARKER, False):
        return

    original_card = official_runtime.build_draft_assistance_card
    original_fields = official_runtime.build_missing_fields_help

    def grounded_card(
        draft: str,
        explanation: str,
        *,
        conversation_language: str,
    ) -> str:
        custom = build_cancellation_assistance_card(
            draft,
            explanation,
            conversation_language=conversation_language,
        )
        if custom is not None:
            return custom
        return original_card(
            draft,
            explanation,
            conversation_language=conversation_language,
        )

    def grounded_fields(
        draft: str,
        *,
        conversation_language: str,
    ) -> str:
        custom = build_cancellation_missing_fields_help(
            draft,
            conversation_language=conversation_language,
        )
        if custom is not None:
            return custom
        return original_fields(
            draft,
            conversation_language=conversation_language,
        )

    official_runtime.build_draft_assistance_card = grounded_card
    official_runtime.build_missing_fields_help = grounded_fields
    setattr(official_runtime, _HELPER_MARKER, True)


def install(core: Any, official_runtime: Any) -> None:
    """Install one idempotent wrapper around the copy-safe official-draft layer."""
    if getattr(core, _CORE_MARKER, False):
        return

    _patch_shared_helpers(official_runtime)
    original_process = core.process_incoming
    original_send = core.send_whatsapp_message

    async def send_whatsapp_message(recipient: str, text: str) -> None:
        state = _ACTIVE_CANCELLATION_DELIVERY.get()
        if state is not None and recipient == state.sender:
            parsed = parse_copy_safe_draft_reply(
                text,
                conversation_language=state.language,
            )
            if parsed is not None:
                grounded = ground_cancellation_draft(
                    state.request_text,
                    parsed.draft,
                    previous_draft=state.previous_draft,
                    conversation_language=state.language,
                )
                if grounded.applicable:
                    if grounded.rejection_reason:
                        logger.error(
                            "Cancellation draft rejected safely",
                            extra={
                                "message_id": state.message_id,
                                "rejection_reason": grounded.rejection_reason,
                            },
                        )
                        raise CopySafeDraftFormatError("cancellation_draft_grounding_failed")
                    explanation = build_cancellation_companion_summary(
                        grounded.draft,
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
        if assistance_action == ASSISTANCE_EXPLAIN and is_cancellation_draft(previous_draft):
            language = _conversation_language(core, message, profile)
            explanation = build_cancellation_plain_explanation(
                previous_draft,
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

        active = bool(
            commands_allowed
            and getattr(message, "message_type", "") == "text"
            and stage == "complete"
            and text.strip()
            and is_official_draft_turn(text, profile)
            and (is_cancellation_request(text) or is_cancellation_draft(previous_draft))
        )
        if not active:
            await original_process(message)
            return

        state = _CancellationDeliveryState(
            sender=message.sender,
            message_id=message.message_id,
            language=_conversation_language(core, message, profile),
            request_text=text,
            previous_draft=previous_draft if is_cancellation_draft(previous_draft) else "",
        )
        token = _ACTIVE_CANCELLATION_DELIVERY.set(state)
        try:
            await original_process(message)
        finally:
            _ACTIVE_CANCELLATION_DELIVERY.reset(token)

    core.send_whatsapp_message = send_whatsapp_message
    core.process_incoming = process_incoming
    setattr(core, _CORE_MARKER, True)
