"""Current-turn language handling for conversational reminders.

The reminder fast path runs before the normal app path updates language state.
This layer makes the current text turn authoritative for reminder replies while
preserving the existing consent and session storage rules.
"""
from __future__ import annotations

import re
from typing import Any

import onboarding as onboarding_rules
import reminder_conversation_extensions as reminders
import sam_product_voice as sam_voice
import sam_conversation_voice as sam_conversation
from pasted_document_grounding import grounded_pasted_invoice_reply

core = reminders.core
base = reminders.base
_ORIGINAL_PROCESS_INCOMING = reminders.process_incoming
_ORIGINAL_EXTRACT_TITLE = reminders._extract_title
_ORIGINAL_IS_NAME_QUESTION = core.is_name_question
_INSTALLED = False

_DOCUMENT_DRAFT_PREFIX = re.compile(
    r"(?:"
    r"(?:اكتب(?:لي)?|صيغ(?:لي)?|صغ|جهز(?:لي)?|اعمل(?:لي)?)\s+(?:رد|جواب|اعتراض)"
    r"|(?:اكتب|قدم|جهز)\s+(?:اعتراض|إلغاء|الغاء)"
    r"|\b(?:schreib|formuliere|antworte|widerspruch|kündig|draft|write|reply|appeal|cancel)\w*\b"
    r"|(?:напиши|сформулюй|відповідь|оскарження)"
    r"|(?:γράψε|σύνταξε|απάντηση|ένσταση)"
    r")",
    re.IGNORECASE,
)
_DOCUMENT_EXPLANATION_PREFIX = re.compile(
    r"(?:"
    r"اشرح|فهمني|شو\s+المطلوب|شو\s+يعني"
    r"|\b(?:erklär|erklaer|was\s+bedeutet|was\s+soll|explain|what\s+does|what\s+do\s+i\s+need)\w*\b"
    r"|(?:поясни|що\s+потрібно)"
    r"|(?:εξήγησε|τι\s+χρειάζεται)"
    r")",
    re.IGNORECASE,
)
_NON_LATIN_SCRIPT = re.compile(r"[\u0370-\u03ff\u0400-\u04ff\u0600-\u06ff]")


def _normalize_name_question(text: str) -> str:
    value = onboarding_rules._normalize(text)
    return re.sub(r"\s+", " ", value).strip()


def _is_user_name_question(text: str) -> bool:
    """Recognize natural variants asking for the user's saved name.

    Keep Sam-name questions (for example ``شو اسمك انت`` / ``Wie heißt du``)
    out of this path so they continue to the product identity layer.
    """
    if _ORIGINAL_IS_NAME_QUESTION(text):
        return True
    normalized = _normalize_name_question(text)
    variants = {
        "شو اسمي انا", "شو اسمي أنا", "بتعرف شو اسمي", "بتتذكر شو اسمي",
        "ما هو اسمي", "ما هو اسمي انا", "ما هو اسمي أنا",
        "wie heiße ich denn", "wie heisse ich denn", "weißt du wie ich heiße", "weisst du wie ich heisse",
        "what is my name again", "do you remember what my name is", "do you know what my name is",
        "як мене звати знову", "ти пам ятаєш як мене звати",
        "πως με λενε ξανα", "θυμασαι πως με λενε",
    }
    return normalized in {_normalize_name_question(item) for item in variants}


def _is_german_reminder_turn(text: str) -> bool:
    """Recognize short German reminder turns that generic language scoring can miss."""
    normalized = str(text or "").casefold()
    return bool(
        re.search(r"\berinnere\s+mich\b", normalized)
        or re.search(r"\bminuten?\b", normalized)
        or re.search(r"\bstunden?\b", normalized)
    )


def detect_turn_language(text: str, profile: dict[str, Any]) -> str:
    fallback = base._language(profile)
    if _is_german_reminder_turn(text):
        return "de"
    return core.detect_language(text, fallback) if str(text or "").strip() else fallback


def clean_english_reminder_title(text: str, title: str) -> str:
    """Drop the infinitive marker left after removing an English time phrase."""
    if re.match(r"^\s*remind\s+me\b", str(text or ""), flags=re.IGNORECASE):
        return re.sub(r"^\s*to\s+", "", str(title or ""), count=1, flags=re.IGNORECASE).strip()
    return str(title or "").strip()


def _extract_title(text: str) -> str:
    return clean_english_reminder_title(text, _ORIGINAL_EXTRACT_TITLE(text))


def prepare_turn_language(message: Any) -> str:
    profile = core.store.get_user(message.sender)
    language = detect_turn_language(message.text, profile)
    previous = base._language(profile)
    if language == previous or str(profile.get("onboarding_stage") or "") != "complete":
        return language

    updates: dict[str, Any] = {
        "session_language": language,
        "session_expires_at": core._session_expiry(),
    }
    if profile.get("memory_consent") == "granted":
        updates["preferred_language"] = language
    core.store.update_user(message.sender, updates)
    return language


def should_clarify_implicit_snooze(intent: Any, recent_count: int) -> bool:
    """Avoid guessing which delivered reminder a bare time-only request refers to."""
    return bool(
        intent is not None
        and getattr(intent, "action", "") == "snooze"
        and getattr(intent, "snooze_implicit", False)
        and getattr(intent, "position", None) is None
        and recent_count > 1
    )


def _first_nonempty_line(text: str) -> str:
    return next(
        (line.strip() for line in str(text or "").splitlines() if line.strip()),
        "",
    )[:240]


def _explicit_document_draft_request(text: str) -> bool:
    """Keep drafting/appeal/cancellation requests on the existing writing path."""
    first_line = _first_nonempty_line(text)
    return bool(first_line and _DOCUMENT_DRAFT_PREFIX.search(first_line))


def _grounded_document_language(text: str, profile: dict[str, Any]) -> str:
    """Do not let the German body of a pasted document overwrite user language."""
    fallback = base._language(profile)
    first_line = _first_nonempty_line(text)
    if not first_line:
        return fallback
    if (
        _DOCUMENT_EXPLANATION_PREFIX.search(first_line)
        or _NON_LATIN_SCRIPT.search(first_line)
    ):
        return detect_turn_language(first_line, profile)
    return fallback


async def _finish_grounded_document_turn(
    message: Any,
    *,
    language: str,
    reply: str,
    profile: dict[str, Any],
) -> None:
    """Persist only bounded context for a deterministic pasted-document reply."""
    updates: dict[str, Any] = {
        "session_language": language,
        "session_topic": "document",
        "session_last_reply": reply,
        "session_expires_at": core._session_expiry(),
        "last_seen": core._now().isoformat(),
    }
    if profile.get("memory_consent") == "granted":
        updates.update({
            "preferred_language": language,
            "current_topic": "document",
            "last_message": "Pasted document text processed transiently",
            "last_message_type": "document",
            "conversation_summary": (
                f"Language={language}; topic=document; "
                "pasted document content processed transiently and not retained"
            ),
        })
    core.store.update_user(message.sender, updates)
    await core._finish(message.message_id, reply, message.sender)


async def process_incoming(message: Any) -> None:
    if message.message_type == "text" and str(message.text or "").strip():
        profile = core.store.get_user(message.sender)
        stage = str(profile.get("onboarding_stage") or "")

        if stage == "complete" and not _explicit_document_draft_request(message.text):
            grounded_language = _grounded_document_language(message.text, profile)
            grounded_reply = grounded_pasted_invoice_reply(
                message.text,
                language=grounded_language,
            )
            if grounded_reply is not None:
                await _finish_grounded_document_turn(
                    message,
                    language=grounded_language,
                    reply=grounded_reply,
                    profile=profile,
                )
                return

        language = prepare_turn_language(message)
        profile = core.store.get_user(message.sender)
        stage = str(profile.get("onboarding_stage") or "")
        if (
            stage == "complete"
            and profile.get("memory_consent") == "granted"
            and base.reminder_delivery_ready(message.sender)
        ):
            intent = reminders.detect_conversational_reminder_intent(message.text)
            if intent is not None and getattr(intent, "snooze_implicit", False):
                recent = base._repository().recent_deliveries(message.sender, limit=10)
                if should_clarify_implicit_snooze(intent, len(recent)) and intent.scheduled_at is not None:
                    core.store.update_user(message.sender, {
                        reminders._PENDING_AT: intent.scheduled_at.isoformat(),
                        "session_language": language,
                        "session_expires_at": core._session_expiry(),
                    })
                    await core._finish(
                        message.message_id,
                        reminders._question(language, "title"),
                        message.sender,
                    )
                    return
    await _ORIGINAL_PROCESS_INCOMING(message)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    reminders._extract_title = _extract_title
    reminders.process_incoming = process_incoming
    base.process_incoming = process_incoming
    core.process_incoming = process_incoming
    core.is_name_question = _is_user_name_question
    sam_voice.install(core)
    sam_conversation.install(core)
    _INSTALLED = True
