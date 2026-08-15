"""Runtime composition for grounded refund, appointment, and contract drafts.

The layer is installed after cancellation grounding and before narrow pasted-document
writing. It never calls an extra provider or executes an external action. It validates
one private copy-safe draft envelope against user-supplied facts, replaces the model
companion with a deterministic localized summary, and serves option-2 explanations
without exposing draft contents in logs.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from draft_assistance import ASSISTANCE_EXPLAIN, detect_draft_assistance_action
from journey_draft_grounding import (
    classify_journey,
    classify_journey_draft,
    ground_journey_draft,
)
from journey_grounding_explanations import (
    build_journey_companion_summary,
    build_journey_plain_explanation,
)
from journey_grounding_patterns import (
    JOURNEY_APPOINTMENT,
    JOURNEY_CONTRACT,
    JOURNEY_REFUND,
)
from official_draft_delivery import (
    DRAFT_MARKER,
    END_MARKER,
    EXPLANATION_MARKER,
    is_official_draft_turn,
    parse_copy_safe_draft_reply,
)

logger = logging.getLogger("amthero24.journey_draft_grounding")

_CORE_MARKER = "_journey_draft_grounding_installed"
_SUPPORTED_LANGUAGES = {"de", "ar", "en", "uk", "el"}
_TARGET_JOURNEYS = {JOURNEY_REFUND, JOURNEY_APPOINTMENT, JOURNEY_CONTRACT}
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
    "session_expires_at",
    "last_message",
    "last_message_type",
}


@dataclass
class _JourneyDeliveryState:
    sender: str
    message_id: str
    language: str
    journey: str
    request_text: str
    previous_draft: str
    profile_before: dict[str, Any]
    responded: bool = False
    failed: bool = False


_ACTIVE_JOURNEY_DELIVERY: ContextVar[_JourneyDeliveryState | None] = ContextVar(
    "amthero24_active_journey_draft_grounding",
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
    return language if language in _SUPPORTED_LANGUAGES else "de"


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


def _restore_profile_after_failure(
    core: Any,
    sender: str,
    before: dict[str, Any],
    previous_draft: str,
) -> None:
    removals = {
        field
        for field in _PROFILE_FIELDS_TO_RESTORE
        if field not in before
    }
    remove_fields = getattr(core.store, "remove_user_fields", None)
    if removals and callable(remove_fields):
        remove_fields(sender, removals)

    updates = {
        field: before[field]
        for field in _PROFILE_FIELDS_TO_RESTORE
        if field in before
    }
    if previous_draft:
        updates["session_last_reply"] = previous_draft
        expiry = _session_expiry(core, before)
        if expiry:
            updates["session_expires_at"] = expiry
        if (
            before.get("memory_consent") == "granted"
            and str(before.get("last_assistant_reply") or "") == previous_draft
        ):
            updates["last_assistant_reply"] = previous_draft
    if updates:
        core.store.update_user(sender, updates)


def _failure_message(language: str) -> str:
    messages = {
        "ar": (
            "لم أرسل المسودة لأن بعض الأرقام أو الادعاءات فيها لم تكن مدعومة "
            "بالمعلومات المؤكدة التي أعطيتني إياها. لم أنفّذ أي إجراء خارجي. "
            "أرسل المعلومة الصحيحة أو اطلب إعادة صياغة المسودة."
        ),
        "de": (
            "Ich habe den Entwurf nicht ausgegeben, weil einzelne Zahlen oder Aussagen "
            "nicht durch deine bestätigten Angaben gedeckt waren. Es wurde keine externe "
            "Aktion ausgeführt. Ergänze die sichere Angabe oder bitte um eine neue Fassung."
        ),
        "en": (
            "I did not provide the draft because some numbers or claims were not supported "
            "by your confirmed information. No external action was taken. Add the verified "
            "detail or ask for a new version."
        ),
        "uk": (
            "Я не надав чернетку, оскільки деякі числа або твердження не підтверджувалися "
            "вашими перевіреними даними. Зовнішніх дій не виконано. Додайте підтверджені "
            "відомості або попросіть нову версію."
        ),
        "el": (
            "Δεν παρείχα το προσχέδιο επειδή ορισμένοι αριθμοί ή ισχυρισμοί δεν "
            "υποστηρίζονταν από τα επιβεβαιωμένα στοιχεία σας. Δεν έγινε εξωτερική "
            "ενέργεια. Προσθέστε το επαληθευμένο στοιχείο ή ζητήστε νέα διατύπωση."
        ),
    }
    return messages.get(language, messages["de"])


def install(core: Any, official_runtime: Any) -> None:
    """Install one idempotent wrapper around the composed official-draft runtime."""
    if getattr(core, _CORE_MARKER, False):
        return

    original_process = core.process_incoming
    original_send = core.send_whatsapp_message

    async def send_whatsapp_message(recipient: str, text: str) -> None:
        state = _ACTIVE_JOURNEY_DELIVERY.get()
        if state is None or recipient != state.sender:
            await original_send(recipient, text)
            return

        if state.responded:
            logger.warning(
                "Additional grounded journey draft delivery suppressed",
                extra={"message_id": state.message_id},
            )
            return

        parsed = parse_copy_safe_draft_reply(
            text,
            conversation_language=state.language,
        )
        if parsed is None:
            await original_send(recipient, text)
            return

        grounded = ground_journey_draft(
            state.request_text,
            parsed.draft,
            previous_draft=state.previous_draft,
            conversation_language=state.language,
        )
        rejection_reason = ""
        if not grounded.applicable or grounded.journey != state.journey:
            rejection_reason = "journey-draft-mismatch"
        elif grounded.rejection_reason:
            rejection_reason = grounded.rejection_reason

        if rejection_reason:
            state.responded = True
            state.failed = True
            logger.warning(
                "Official journey draft rejected safely",
                extra={
                    "message_id": state.message_id,
                    "journey": state.journey,
                    "rejection_reason": rejection_reason,
                },
            )
            await original_send(recipient, _failure_message(state.language))
            core.store.update_message_status(state.message_id, "sent")
            return

        explanation = build_journey_companion_summary(
            grounded.draft,
            conversation_language=parsed.conversation_language,
        )
        if explanation is None:
            state.responded = True
            state.failed = True
            logger.warning(
                "Official journey companion could not be grounded",
                extra={
                    "message_id": state.message_id,
                    "journey": state.journey,
                    "rejection_reason": "deterministic-companion-unavailable",
                },
            )
            await original_send(recipient, _failure_message(state.language))
            core.store.update_message_status(state.message_id, "sent")
            return

        state.responded = True
        envelope = (
            f"{DRAFT_MARKER}\n{grounded.draft}\n"
            f"{EXPLANATION_MARKER}\n{explanation}\n{END_MARKER}"
        )
        await original_send(recipient, envelope)

    async def process_incoming(message: Any) -> None:
        profile = core.store.get_user(message.sender)
        stage = str(profile.get("onboarding_stage") or "")
        text = str(getattr(message, "text", "") or "")
        commands_allowed = getattr(message, "internal_context", "") != "document_analysis"
        supported_text_type = getattr(message, "message_type", "") in {
            "text",
            "button",
            "interactive",
        }
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
        previous_journey = classify_journey_draft(previous_draft)
        if (
            assistance_action == ASSISTANCE_EXPLAIN
            and previous_journey in _TARGET_JOURNEYS
        ):
            language = _conversation_language(core, message, profile)
            explanation = build_journey_plain_explanation(
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

        journey = classify_journey(text, previous_draft)
        active = bool(
            commands_allowed
            and getattr(message, "message_type", "") == "text"
            and stage == "complete"
            and text.strip()
            and journey in _TARGET_JOURNEYS
            and is_official_draft_turn(text, profile)
        )
        if not active:
            await original_process(message)
            return

        state = _JourneyDeliveryState(
            sender=message.sender,
            message_id=message.message_id,
            language=_conversation_language(core, message, profile),
            journey=str(journey),
            request_text=text,
            previous_draft=(
                previous_draft
                if classify_journey_draft(previous_draft) == journey
                else ""
            ),
            profile_before=dict(profile),
        )
        token = _ACTIVE_JOURNEY_DELIVERY.set(state)
        try:
            await original_process(message)
        finally:
            _ACTIVE_JOURNEY_DELIVERY.reset(token)

        if state.failed:
            try:
                _restore_profile_after_failure(
                    core,
                    message.sender,
                    state.profile_before,
                    state.previous_draft,
                )
            except Exception:
                logger.exception(
                    "Unable to restore draft context after grounded journey rejection",
                    extra={"message_id": message.message_id},
                )

    core.send_whatsapp_message = send_whatsapp_message
    core.process_incoming = process_incoming
    setattr(core, _CORE_MARKER, True)
