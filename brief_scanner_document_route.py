"""Read-only orchestration for Brief Scanner document handling.

This module is intentionally independent from FastAPI, WhatsApp, persistence, missions, reminders,
and telemetry. It converts one downloaded image into a bounded user-facing reply while preserving
all fail-closed decisions made by the provider and model boundary.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Final

from brief_scanner_groq_provider import extract_brief_with_groq
from brief_scanner_model_boundary import BriefScannerBoundaryOutcome, BriefScannerBoundaryStatus


@dataclass(frozen=True)
class BriefScannerRouteResult:
    handled: bool
    reply: str = ""
    outcome: BriefScannerBoundaryOutcome | None = None

    @property
    def allows_side_effects(self) -> bool:
        """Route v1 is read-only even when extraction itself passed the language quality gate."""
        return False


ProviderCall = Callable[..., BriefScannerBoundaryOutcome]

_SUPPORTED_IMAGE_MIME_TYPES: Final[frozenset[str]] = frozenset({
    "image/jpeg",
    "image/png",
    "image/webp",
})


def _localize(response_language: str, *, ar: str, de: str, en: str) -> str:
    base = response_language.split("-", 1)[0].casefold()
    if base == "ar":
        return ar
    if base == "en":
        return en
    return de


def _format_date(value: date | None) -> str:
    return value.isoformat() if value is not None else ""


def _format_amount(amount_minor: int | None, currency: str) -> str:
    if amount_minor is None:
        return ""
    major = amount_minor / 100
    return f"{major:.2f} {currency}"


def _validated_reply(outcome: BriefScannerBoundaryOutcome, response_language: str) -> str:
    facts = outcome.facts
    if facts is None:
        return _localize(
            response_language,
            ar="تعذّر استخراج معلومات موثوقة من الوثيقة.",
            de="Aus dem Dokument konnten keine verlässlichen Angaben extrahiert werden.",
            en="No reliable information could be extracted from the document.",
        )

    heading = _localize(
        response_language,
        ar="ملخص الوثيقة:",
        de="Dokumentübersicht:",
        en="Document overview:",
    )
    labels = {
        "sender": _localize(response_language, ar="الجهة", de="Absender", en="Sender"),
        "deadline": _localize(response_language, ar="آخر موعد", de="Frist", en="Deadline"),
        "appointment": _localize(response_language, ar="الموعد", de="Termin", en="Appointment"),
        "action": _localize(response_language, ar="المطلوب", de="Erforderlich", en="Required action"),
        "amount": _localize(response_language, ar="المبلغ", de="Betrag", en="Amount"),
        "reference": _localize(response_language, ar="المرجع", de="Aktenzeichen", en="Reference"),
    }
    values = (
        (labels["sender"], facts.sender_organization),
        (labels["deadline"], _format_date(facts.deadline)),
        (labels["appointment"], _format_date(facts.appointment_date)),
        (labels["action"], facts.requested_action),
        (labels["amount"], _format_amount(facts.amount_minor, facts.currency)),
        (labels["reference"], facts.reference_number),
    )
    lines = [f"{label}: {value}" for label, value in values if value]
    if not lines:
        lines.append(_localize(
            response_language,
            ar="لم أجد موعدًا أو إجراءً واضحًا في الصورة.",
            de="In der Abbildung wurde keine klare Frist oder Handlung gefunden.",
            en="No clear deadline or action was found in the image.",
        ))
    if outcome.status == BriefScannerBoundaryStatus.VALIDATED_READ_ONLY:
        lines.append(_localize(
            response_language,
            ar="تم تقديم هذا الشرح للقراءة فقط؛ لن يتم إنشاء مهمة أو تذكير تلقائيًا.",
            de="Diese Auswertung ist schreibgeschützt; es wird keine Aufgabe oder Erinnerung automatisch erstellt.",
            en="This analysis is read-only; no task or reminder will be created automatically.",
        ))
    return heading + "\n" + "\n".join(lines)


def _outcome_reply(outcome: BriefScannerBoundaryOutcome, response_language: str) -> str:
    if outcome.status in {BriefScannerBoundaryStatus.VALIDATED, BriefScannerBoundaryStatus.VALIDATED_READ_ONLY}:
        return _validated_reply(outcome, response_language)
    if outcome.status == BriefScannerBoundaryStatus.RETRYABLE_DOCUMENT_QUALITY:
        return _localize(
            response_language,
            ar="الصورة غير واضحة أو تبدو صفحات ناقصة. أرسل صورة أوضح وكاملة لكل صفحة.",
            de="Das Bild ist nicht klar genug oder Seiten scheinen zu fehlen. Bitte sende jede Seite vollständig und schärfer.",
            en="The image is unclear or pages appear to be missing. Please send every page clearly and completely.",
        )
    if outcome.status == BriefScannerBoundaryStatus.BLOCKED_OR_ESCALATED:
        return _localize(
            response_language,
            ar="تبدو الوثيقة حساسة أو عاجلة. لن أنشئ إجراءً تلقائيًا؛ احصل على مساعدة مختصة بسرعة.",
            de="Das Dokument wirkt sensibel oder dringend. Es wird keine automatische Aktion erstellt; hole bitte zeitnah fachliche Hilfe.",
            en="The document appears sensitive or urgent. No automatic action will be created; seek qualified help promptly.",
        )
    return _localize(
        response_language,
        ar="تعذّر تحليل الوثيقة بأمان حاليًا. أرسل صورة أوضح أو حاول لاحقًا.",
        de="Das Dokument konnte derzeit nicht sicher analysiert werden. Bitte sende ein klareres Bild oder versuche es später erneut.",
        en="The document could not be analyzed safely right now. Send a clearer image or try again later.",
    )


def handle_brief_scanner_document(
    *,
    image_bytes: bytes,
    mime_type: str,
    response_language: str,
    provider: ProviderCall = extract_brief_with_groq,
    enabled: bool | None = None,
) -> BriefScannerRouteResult:
    """Handle one image in read-only mode; non-image documents remain on the existing route."""
    normalized_mime = (mime_type or "").strip().casefold()
    if normalized_mime not in _SUPPORTED_IMAGE_MIME_TYPES:
        return BriefScannerRouteResult(handled=False)

    outcome = provider(
        image_bytes=image_bytes,
        mime_type=normalized_mime,
        response_language=response_language,
        enabled=enabled,
    )
    if outcome.error_code == "brief_scanner_provider_disabled":
        return BriefScannerRouteResult(handled=False, outcome=outcome)
    return BriefScannerRouteResult(
        handled=True,
        reply=_outcome_reply(outcome, response_language),
        outcome=outcome,
    )
