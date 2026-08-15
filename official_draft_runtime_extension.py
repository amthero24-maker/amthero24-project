"""Runtime composition for copy-safe drafts and understand-before-send assistance.

The wrapper is installed inside the existing WhatsApp application boundary. It uses
context-local state, never changes provider/runtime flags, and only separates an
already-generated official draft from its companion or routes a bounded read-only
follow-up choice while preserving the clean draft as short-lived conversation context.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass, is_dataclass, replace
from types import SimpleNamespace
from typing import Any

from draft_assistance import (
    ASSISTANCE_FIELDS,
    DraftAssistanceFormatError,
    activate_draft_assistance,
    build_draft_assistance_card,
    build_missing_fields_help,
    detect_draft_assistance_action,
    draft_assistance_uses_model,
    parse_draft_assistance_reply,
    reset_draft_assistance,
)
from official_draft_delivery import (
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
_ASSISTANCE_INTERNAL_CONTEXT = "official_draft_assistance"


@dataclass
class _DeliveryState:
    sender: str
    message_id: str
    language: str
    parsed: CopySafeDraftReply | None = None


@dataclass
class _AssistanceDeliveryState:
    sender: str
    message_id: str
    language: str
    action: str
    draft: str
    responded: bool = False
    failed: bool = False


_ACTIVE_DELIVERY: ContextVar[_DeliveryState | None] = ContextVar(
    "amthero24_active_official_draft_delivery",
    default=None,
)
_ACTIVE_ASSISTANCE_DELIVERY: ContextVar[_AssistanceDeliveryState | None] = ContextVar(
    "amthero24_active_official_draft_assistance_delivery",
    default=None,
)

_PROFILE_FIELDS_TO_RESTORE = {
    "first_name",
    "city",
    "preferred_language",
    "current_topic",
    "last_assistant_reply",
    "conversation_summary",
    "communication_style",
    "session_language",
    "session_topic",
    "session_last_reply",
    "last_message",
    "last_message_type",
}


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


def _session_expiry(core: Any, profile: dict[str, Any]) -> str:
    builder = getattr(core, "_session_expiry", None)
    if callable(builder):
        try:
            return str(builder())
        except Exception:
            logger.warning("Unable to refresh draft-assistance session expiry")
    return str(profile.get("session_expires_at") or "")


def _draft_is_reusable(profile: dict[str, Any], draft: str) -> bool:
    """Return true only when this exact draft was already reusable before assistance."""
    return bool(
        profile.get("memory_consent") == "granted"
        and str(profile.get("last_assistant_reply") or "") == str(draft or "")
    )


def _clean_draft_updates(
    core: Any,
    profile: dict[str, Any],
    draft: str,
    *,
    persist_reusable: bool,
) -> dict[str, Any]:
    updates: dict[str, Any] = {"session_last_reply": draft}
    expiry = _session_expiry(core, profile)
    if expiry:
        updates["session_expires_at"] = expiry
    if persist_reusable and profile.get("memory_consent") == "granted":
        updates["last_assistant_reply"] = draft
    return updates


def _retain_clean_draft_context(
    core: Any,
    message: Any,
    profile: dict[str, Any],
    parsed: CopySafeDraftReply | None,
) -> None:
    """Replace any raw marker/mixed reply retained by the existing app path."""
    if parsed is None:
        return
    core.store.update_user(
        message.sender,
        _clean_draft_updates(
            core,
            profile,
            parsed.draft,
            persist_reusable=True,
        ),
    )


def _restore_profile_after_assistance(
    core: Any,
    sender: str,
    before: dict[str, Any],
    draft: str,
) -> None:
    """Undo synthetic processing metadata while keeping the clean draft available."""
    persist_reusable = _draft_is_reusable(before, draft)
    removals = {
        field
        for field in _PROFILE_FIELDS_TO_RESTORE
        if field not in before
        and field not in {"session_last_reply", "last_assistant_reply"}
    }
    if not persist_reusable and "last_assistant_reply" not in before:
        removals.add("last_assistant_reply")
    if removals:
        core.store.remove_user_fields(sender, removals)

    updates = {
        field: before[field]
        for field in _PROFILE_FIELDS_TO_RESTORE
        if field in before
    }
    updates.update(
        _clean_draft_updates(
            core,
            before,
            draft,
            persist_reusable=persist_reusable,
        )
    )
    core.store.update_user(sender, updates)


def _assistance_internal_text(language: str) -> str:
    return {
        "ar": "متابعة-فهم-المسودة: مساعدة",
        "de": "Entwurf-Verständnishilfe: Auswahl",
        "en": "Draft-understanding-help: selection",
        "uk": "Допомога-з-розумінням-чернетки: вибір",
        "el": "Βοήθεια-κατανόησης-προσχεδίου: επιλογή",
    }.get(language, "Draft-understanding-help: selection")


def _assistance_message(message: Any, language: str) -> Any:
    updates = {
        "text": _assistance_internal_text(language),
        "internal_context": _ASSISTANCE_INTERNAL_CONTEXT,
    }
    if is_dataclass(message):
        return replace(message, **updates)
    values = dict(vars(message))
    values.update(updates)
    return SimpleNamespace(**values)


def install(core: Any) -> None:
    """Install one context-safe wrapper around the current application path."""
    if getattr(core, _CORE_MARKER, False):
        return

    original_process = core.process_incoming
    original_send = core.send_whatsapp_message

    async def send_whatsapp_message(recipient: str, text: str) -> None:
        assistance_state = _ACTIVE_ASSISTANCE_DELIVERY.get()
        if assistance_state is not None and recipient == assistance_state.sender:
            if assistance_state.failed or assistance_state.responded:
                await original_send(recipient, text)
                return
            parsed_assistance = parse_draft_assistance_reply(
                text,
                action=assistance_state.action,
                conversation_language=assistance_state.language,
            )
            if parsed_assistance is None:
                assistance_state.failed = True
                logger.error(
                    "Draft assistance reply violated its private envelope",
                    extra={"message_id": assistance_state.message_id},
                )
                raise DraftAssistanceFormatError("official_draft_assistance_reply_ambiguous")
            assistance_state.responded = True
            await original_send(recipient, parsed_assistance)
            core.store.update_message_status(assistance_state.message_id, "sent")
            return

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
        companion = build_draft_assistance_card(
            parsed.draft,
            parsed.explanation,
            conversation_language=parsed.conversation_language,
        )
        await original_send(recipient, parsed.draft)
        core.store.update_message_status(state.message_id, "sent")
        try:
            await original_send(recipient, companion)
        except Exception:
            logger.warning(
                "Secondary official draft companion delivery failed",
                extra={"message_id": state.message_id},
            )

    async def process_incoming(message: Any) -> None:
        profile = core.store.get_user(message.sender)
        stage = str(profile.get("onboarding_stage") or "")
        commands_allowed = getattr(message, "internal_context", "") != "document_analysis"
        supported_text_type = getattr(message, "message_type", "") in {"text", "button", "interactive"}
        previous_draft = str(
            profile.get("session_last_reply")
            or profile.get("last_assistant_reply")
            or ""
        )
        assistance_action = (
            detect_draft_assistance_action(str(message.text or ""), previous_draft)
            if commands_allowed
            and supported_text_type
            and stage == "complete"
            and str(message.text or "").strip()
            else None
        )

        if assistance_action is not None:
            language = _conversation_language(core, message, profile)
            persist_reusable = _draft_is_reusable(profile, previous_draft)
            if assistance_action == ASSISTANCE_FIELDS:
                await original_send(
                    message.sender,
                    build_missing_fields_help(
                        previous_draft,
                        conversation_language=language,
                    ),
                )
                core.store.update_message_status(message.message_id, "sent")
                core.store.update_user(
                    message.sender,
                    _clean_draft_updates(
                        core,
                        profile,
                        previous_draft,
                        persist_reusable=persist_reusable,
                    ),
                )
                return

            if draft_assistance_uses_model(assistance_action):
                state = _AssistanceDeliveryState(
                    sender=message.sender,
                    message_id=message.message_id,
                    language=language,
                    action=assistance_action,
                    draft=previous_draft,
                )
                delivery_token = _ACTIVE_ASSISTANCE_DELIVERY.set(state)
                assistance_token = activate_draft_assistance(
                    action=assistance_action,
                    draft=previous_draft,
                    conversation_language=language,
                )
                try:
                    await original_process(_assistance_message(message, language))
                finally:
                    reset_draft_assistance(assistance_token)
                    _ACTIVE_ASSISTANCE_DELIVERY.reset(delivery_token)
                    try:
                        _restore_profile_after_assistance(
                            core,
                            message.sender,
                            profile,
                            previous_draft,
                        )
                    except Exception:
                        logger.exception(
                            "Unable to restore clean draft context after assistance",
                            extra={"message_id": message.message_id},
                        )
                return

        active = bool(
            commands_allowed
            and getattr(message, "message_type", "") == "text"
            and stage == "complete"
            and str(message.text or "").strip()
            and is_official_draft_turn(str(message.text or ""), profile)
        )

        if not active:
            await original_process(message)
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

        _retain_clean_draft_context(core, message, profile, state.parsed)

    core.send_whatsapp_message = send_whatsapp_message
    core.process_incoming = process_incoming
    setattr(core, _CORE_MARKER, True)
