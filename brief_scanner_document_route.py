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
_SUPPORTED_REPLY_LANGUAGES: Final[frozenset[str]] = frozenset({"ar", "de", "en", "uk", "el"})
_ZERO_DECIMAL_CURRENCIES: Final[frozenset[str]] = frozenset({
    "BIF", "CLP", "DJF", "GNF", "ISK", "JPY", "KMF", "KRW", "PYG", "RWF", "UGX", "UYI", "VND", "VUV", "XAF", "XOF", "XPF",
})
_THREE_DECIMAL_CURRENCIES: Final[frozenset[str]] = frozenset({"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"})


def _language_base(response_language: str) -> str:
    return (response_language or "").split("-", 1)[0].casefold()


def _localize(response_language: str, *, ar: str, de: str, en: str, uk: str, el: str) -> str:
    return {
        "ar": ar,
        "de": de,
        "en": en,
        "uk": uk,
        "el": el,
    }[_language_base(response_language)]


def _format_date(value: date | None) -> str:
    return value.isoformat() if value is not None else ""


def _format_amount(amount_minor: int | None, currency: str) -> str:
    if amount_minor is None:
        return ""
    normalized_currency = currency.upper()
    exponent = 0 if normalized_currency in _ZERO_DECIMAL_CURRENCIES else 3 if normalized_currency in _THREE_DECIMAL_CURRENCIES else 2
    if exponent == 0:
        return f"{amount_minor} {normalized_currency}"
    divisor = 10 ** exponent
    major, minor = divmod(amount_minor, divisor)
    return f"{major}.{minor:0{exponent}d} {normalized_currency}"


def _validated_reply(outcome: BriefScannerBoundaryOutcome, response_language: str) -> str:
    facts = outcome.facts
    if facts is None:
        return _localize(
            response_language,
            ar="تعذّر استخراج معلومات موثوقة من الوثيقة.",
            de="Aus dem Dokument konnten keine verlässlichen Angaben extrahiert werden.",
            en="No reliable information could be extracted from the document.",
            uk="Не вдалося отримати надійну інформацію з документа.",
            el="Δεν ήταν δυνατή η εξαγωγή αξιόπιστων πληροφοριών από το έγγραφο.",
        )

    heading = _localize(
        response_language,
        ar="ملخص الوثيقة:",
        de="Dokumentübersicht:",
        en="Document overview:",
        uk="Огляд документа:",
        el="Επισκόπηση εγγράφου:",
    )
    labels = {
        "sender": _localize(response_language, ar="الجهة", de="Absender", en="Sender", uk="Відправник", el="Αποστολέας"),
        "deadline": _localize(response_language, ar="آخر موعد", de="Frist", en="Deadline", uk="Кінцевий строк", el="Προθεσμία"),
        "appointment": _localize(response_language, ar="الموعد", de="Termin", en="Appointment", uk="Зустріч", el="Ραντεβού"),
        "action": _localize(response_language, ar="المطلوب", de="Erforderlich", en="Required action", uk="Необхідна дія", el="Απαιτούμενη ενέργεια"),
        "amount": _localize(response_language, ar="المبلغ", de="Betrag", en="Amount", uk="Сума", el="Ποσό"),
        "reference": _localize(response_language, ar="المرجع", de="Aktenzeichen", en="Reference", uk="Номер справи", el="Αριθμός αναφοράς"),
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
            uk="На зображенні не знайдено чіткого строку або необхідної дії.",
            el="Δεν βρέθηκε σαφής προθεσμία ή απαιτούμενη ενέργεια στην εικόνα.",
        ))
    if outcome.status == BriefScannerBoundaryStatus.VALIDATED_READ_ONLY:
        lines.append(_localize(
            response_language,
            ar="تم تقديم هذا الشرح للقراءة فقط؛ لن يتم إنشاء مهمة أو تذكير تلقائيًا.",
            de="Diese Auswertung ist schreibgeschützt; es wird keine Aufgabe oder Erinnerung automatisch erstellt.",
            en="This analysis is read-only; no task or reminder will be created automatically.",
            uk="Цей аналіз доступний лише для читання; завдання чи нагадування автоматично не створюються.",
            el="Αυτή η ανάλυση είναι μόνο για ανάγνωση· δεν δημιουργείται αυτόματα εργασία ή υπενθύμιση.",
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
            uk="Зображення нечітке або бракує сторінок. Надішли кожну сторінку повністю та чіткіше.",
            el="Η εικόνα δεν είναι αρκετά καθαρή ή λείπουν σελίδες. Στείλε κάθε σελίδα ολόκληρη και πιο καθαρή.",
        )
    if outcome.status == BriefScannerBoundaryStatus.BLOCKED_OR_ESCALATED:
        return _localize(
            response_language,
            ar="تبدو الوثيقة حساسة أو عاجلة. لن أنشئ إجراءً تلقائيًا؛ احصل على مساعدة مختصة بسرعة.",
            de="Das Dokument wirkt sensibel oder dringend. Es wird keine automatische Aktion erstellt; hole bitte zeitnah fachliche Hilfe.",
            en="The document appears sensitive or urgent. No automatic action will be created; seek qualified help promptly.",
            uk="Документ виглядає чутливим або терміновим. Автоматична дія не створюється; якнайшвидше звернися по фахову допомогу.",
            el="Το έγγραφο φαίνεται ευαίσθητο ή επείγον. Δεν δημιουργείται αυτόματη ενέργεια· ζήτησε σύντομα εξειδικευμένη βοήθεια.",
        )
    return _localize(
        response_language,
        ar="تعذّر تحليل الوثيقة بأمان حاليًا. أرسل صورة أوضح أو حاول لاحقًا.",
        de="Das Dokument konnte derzeit nicht sicher analysiert werden. Bitte sende ein klareres Bild oder versuche es später erneut.",
        en="The document could not be analyzed safely right now. Send a clearer image or try again later.",
        uk="Зараз документ не вдалося безпечно проаналізувати. Надішли чіткіше зображення або спробуй пізніше.",
        el="Το έγγραφο δεν ήταν δυνατό να αναλυθεί με ασφάλεια τώρα. Στείλε καθαρότερη εικόνα ή δοκίμασε αργότερα.",
    )


def handle_brief_scanner_document(
    *,
    image_bytes: bytes,
    mime_type: str,
    response_language: str,
    provider: ProviderCall = extract_brief_with_groq,
    enabled: bool | None = None,
) -> BriefScannerRouteResult:
    """Handle one image in read-only mode; unsupported reply languages use the existing route."""
    normalized_mime = (mime_type or "").strip().casefold()
    if normalized_mime not in _SUPPORTED_IMAGE_MIME_TYPES:
        return BriefScannerRouteResult(handled=False)
    if _language_base(response_language) not in _SUPPORTED_REPLY_LANGUAGES:
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
