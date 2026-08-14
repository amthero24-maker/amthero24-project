"""Deterministic, copy-safe grounding for narrow pasted-document writing requests.

This layer handles only high-confidence requests for missing payment information in a
pasted German invoice/letter. It preserves the source document type, emits the
reviewable German draft as its own WhatsApp message, and keeps the pasted source out
of reusable memory. It performs no external sending beyond replying to the user and
does not enable any action runtime.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from pasted_document_grounding import PastedInvoiceFacts, extract_pasted_invoice_facts

logger = logging.getLogger("amthero24.writing_grounding")

_SUPPORTED_LANGUAGES = {"de", "ar", "en", "uk", "el"}
_CORE_MARKER = "_writing_grounding_installed"
_DRAFT_REQUEST = re.compile(
    r"(?:"
    r"(?:اكتب(?:لي)?|صيغ(?:لي)?|جهز(?:لي)?|اعمل(?:لي)?)\s+(?:رد|رسالة|ايميل|إيميل|جواب)"
    r"|\b(?:schreib|formuliere|antworte|draft|write|reply)\w*\b"
    r"|(?:напиши|сформулюй|відповідь)"
    r"|(?:γράψε|σύνταξε|απάντηση)"
    r")",
    re.IGNORECASE,
)
_PAYMENT_INFORMATION_REQUEST = re.compile(
    r"(?:"
    r"بيانات\s+(?:الحساب|الدفع)|رقم\s+الحساب|غرض\s+التحويل|مرجع\s+التحويل|"
    r"(?:ال)?(?:آيبان|ايبان)|"
    r"\b(?:iban|bic|bankverbindung|zahlungsinformationen|verwendungszweck|"
    r"bank\s+details|payment\s+details|payment\s+reference)\b|"
    r"банківськ\w*\s+реквізит\w*|призначенн\w*\s+платеж\w*|"
    r"τραπεζικ\w*\s+στοιχεί\w*|αιτιολογί\w*\s+πληρωμ\w*"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GroundedWritingReply:
    draft: str
    explanation: str
    conversation_language: str


def _instruction_prefix(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    return re.split(r"\n\s*\n", normalized, maxsplit=1)[0][:700].strip()


def _bounded(value: str, limit: int = 120) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _conversation_language(core: Any, text: str, profile: dict[str, Any]) -> str:
    fallback = str(
        profile.get("preferred_language")
        or profile.get("session_language")
        or "de"
    )
    prefix = _instruction_prefix(text)
    language = core.detect_language(prefix, fallback) if prefix else fallback
    return language if language in _SUPPORTED_LANGUAGES else "de"


def _is_payment_information_draft_request(text: str) -> bool:
    prefix = _instruction_prefix(text)
    return bool(
        prefix
        and _DRAFT_REQUEST.search(prefix)
        and _PAYMENT_INFORMATION_REQUEST.search(prefix)
    )


def _source_reference(facts: PastedInvoiceFacts) -> str:
    reference = "vielen Dank für Ihr Schreiben"
    if facts.document_date:
        reference += f" vom {_bounded(facts.document_date, 40)}"
    if facts.subject:
        reference += f" mit dem Betreff „{_bounded(facts.subject)}“"
    return reference + "."


def _fact_sentence(facts: PastedInvoiceFacts) -> str:
    amount = _bounded(facts.amount, 50)
    deadline = _bounded(facts.deadline, 50)
    if amount and deadline:
        return (
            f"Darin wird ein offener Betrag von {amount} genannt; "
            f"als Zahlungsfrist ist der {deadline} angegeben."
        )
    if amount:
        return f"Darin wird ein offener Betrag von {amount} genannt."
    if deadline:
        return f"Als Zahlungsfrist ist der {deadline} angegeben."
    return ""


def _missing_information_request(
    *,
    missing_bank: bool,
    missing_purpose: bool,
) -> str:
    if missing_bank and missing_purpose:
        return (
            "In dem mir vorliegenden Schreiben sind weder die Bankverbindung noch "
            "der für die Überweisung anzugebende Verwendungszweck enthalten. "
            "Bitte teilen Sie mir beide Angaben mit, damit ich die Zahlung korrekt "
            "vornehmen kann."
        )
    if missing_bank:
        return (
            "In dem mir vorliegenden Schreiben ist keine Bankverbindung enthalten. "
            "Bitte teilen Sie mir die für die Überweisung erforderliche "
            "Bankverbindung mit."
        )
    return (
        "In dem mir vorliegenden Schreiben ist kein Verwendungszweck für die "
        "Überweisung angegeben. Bitte teilen Sie mir den anzugebenden "
        "Verwendungszweck mit."
    )


def _render_draft(facts: PastedInvoiceFacts) -> str | None:
    missing_bank = not facts.bank_details_present
    missing_purpose = not (
        facts.payment_purpose
        or facts.customer_number_explicitly_assigned_as_purpose
    )
    if not (missing_bank or missing_purpose):
        return None

    subject = "Betreff: Rückfrage zu den Zahlungsinformationen"
    if facts.customer_number:
        subject += f" – Kundennummer {_bounded(facts.customer_number, 80)}"

    paragraphs = [
        subject,
        "",
        "Sehr geehrte Damen und Herren,",
        "",
        _source_reference(facts),
    ]
    fact_sentence = _fact_sentence(facts)
    if fact_sentence:
        paragraphs.extend(["", fact_sentence])
    paragraphs.extend([
        "",
        _missing_information_request(
            missing_bank=missing_bank,
            missing_purpose=missing_purpose,
        ),
        "",
        "Mit freundlichen Grüßen",
        "",
        "[Ihr Vor- und Nachname]",
    ])
    return "\n".join(paragraphs)


def _explanation(language: str) -> str:
    messages = {
        "ar": (
            "المسودة بالرسالة السابقة منفصلة حتى تقدر تنسخها مباشرة. "
            "هي تطلب فقط بيانات الحساب وغرض التحويل، ولم يتم إرسالها. "
            "راجع اسمك ثم أرسلها بنفسك إذا كانت مناسبة."
        ),
        "de": (
            "Der Entwurf steht in der vorherigen Nachricht allein, damit du ihn "
            "direkt kopieren kannst. Er fragt nur nach Bankverbindung und "
            "Verwendungszweck und wurde nicht versendet."
        ),
        "en": (
            "The draft is in the previous message by itself so you can copy it "
            "directly. It only asks for the bank details and payment reference, "
            "and it has not been sent."
        ),
        "uk": (
            "Чернетка в попередньому повідомленні окремо, щоб її можна було легко "
            "скопіювати. Вона лише запитує банківські реквізити та призначення "
            "платежу і не була надіслана."
        ),
        "el": (
            "Το προσχέδιο βρίσκεται μόνο του στο προηγούμενο μήνυμα για εύκολη "
            "αντιγραφή. Ζητά μόνο τα τραπεζικά στοιχεία και την αιτιολογία πληρωμής "
            "και δεν έχει σταλεί."
        ),
    }
    return messages.get(language, messages["de"])


def build_grounded_payment_information_draft(
    text: str,
    *,
    conversation_language: str,
) -> GroundedWritingReply | None:
    """Return a safe split reply for one strict pasted-invoice writing intent."""
    if not _is_payment_information_draft_request(text):
        return None
    facts = extract_pasted_invoice_facts(text)
    if facts is None:
        return None
    draft = _render_draft(facts)
    if draft is None:
        return None
    language = (
        conversation_language
        if conversation_language in _SUPPORTED_LANGUAGES
        else "de"
    )
    return GroundedWritingReply(
        draft=draft,
        explanation=_explanation(language),
        conversation_language=language,
    )


def _persist_transient_context(
    core: Any,
    message: Any,
    profile: dict[str, Any],
    reply: GroundedWritingReply,
) -> None:
    updates: dict[str, Any] = {
        "session_language": reply.conversation_language,
        "session_topic": "document",
        "session_last_reply": reply.draft,
        "session_expires_at": core._session_expiry(),
        "last_seen": core._now().isoformat(),
    }
    if profile.get("memory_consent") == "granted":
        updates.update({
            "preferred_language": reply.conversation_language,
            "current_topic": "document",
            "last_message": "Pasted document draft processed transiently",
            "last_message_type": "document",
            "conversation_summary": (
                f"Language={reply.conversation_language}; topic=document; "
                "pasted document draft processed transiently and not retained"
            ),
        })
    core.store.update_user(message.sender, updates)


async def _deliver_copy_safe_reply(
    core: Any,
    message: Any,
    reply: GroundedWritingReply,
) -> None:
    """Make the primary draft retry-safe; the explanatory message is secondary."""
    await core._finish(message.message_id, reply.draft, message.sender)
    try:
        await core.send_whatsapp_message(message.sender, reply.explanation)
    except Exception:
        logger.warning(
            "Secondary grounded draft explanation delivery failed",
            extra={"message_id": message.message_id},
        )


def install(core: Any) -> None:
    """Install one idempotent wrapper around the current composed message path."""
    if getattr(core, _CORE_MARKER, False):
        return
    original = core.process_incoming

    async def process_incoming(message: Any) -> None:
        text = str(getattr(message, "text", "") or "")
        profile = core.store.get_user(message.sender)
        stage = str(profile.get("onboarding_stage") or "")
        commands_allowed = getattr(message, "internal_context", "") != "document_analysis"
        if (
            commands_allowed
            and getattr(message, "message_type", "") == "text"
            and stage == "complete"
            and text.strip()
        ):
            language = _conversation_language(core, text, profile)
            reply = build_grounded_payment_information_draft(
                text,
                conversation_language=language,
            )
            if reply is not None:
                _persist_transient_context(core, message, profile, reply)
                await _deliver_copy_safe_reply(core, message, reply)
                return
        await original(message)

    core.process_incoming = process_incoming
    setattr(core, _CORE_MARKER, True)
