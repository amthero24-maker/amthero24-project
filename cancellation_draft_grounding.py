"""Deterministic grounding for copy-safe cancellation drafts and assistance.

This module is read-only. It does not call a model, persist data, send WhatsApp
messages, execute cancellations, or activate any action runtime. It narrows generated
cancellation text to verified user facts and produces localized assistance without
reinterpreting placeholders or inventing payment, timing, or legal instructions.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final

from cancellation_contract import NEXT_POSSIBLE_DATE_WORDING

_SUPPORTED_LANGUAGES: Final[frozenset[str]] = frozenset({"de", "ar", "en", "uk", "el"})
_DATE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?<!\w)\d{1,2}[./-]\d{1,2}[./-]\d{2,4}(?!\w)"
)
_IDENTIFIER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w@])"
    r"(?=[A-Za-z0-9ÄÖÜäöüß_-]{4,50}(?![\w@]))"
    r"(?=[A-Za-z0-9ÄÖÜäöüß_-]*[A-Za-zÄÖÜäöüß])"
    r"(?=[A-Za-z0-9ÄÖÜäöüß_-]*\d)"
    r"[A-Za-z0-9ÄÖÜäöüß][A-Za-z0-9ÄÖÜäöüß_-]{3,49}"
    r"(?![\w@])"
)
_COMPANY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9&.'/-]*"
    r"(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9&.'/-]*){0,5}\s+"
    r"(?:GmbH|AG|UG(?:\s*\(haftungsbeschränkt\))?|KG|OHG|GbR|e\.V\.)\b"
)
_PLACEHOLDER_PATTERN: Final[re.Pattern[str]] = re.compile(r"\[([^\[\]\n]{2,100})\]")

_CANCELLATION_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?:kündig|kuendig)", re.IGNORECASE),
    re.compile(r"(?:إلغاء|الغاء|ألغي|الغي|إلغي|فسخ)", re.IGNORECASE),
    re.compile(r"\b(?:cancel|cancellation|terminate|termination)\b", re.IGNORECASE),
    re.compile(r"(?:скасув|розірв|припинен)", re.IGNORECASE),
    re.compile(r"(?:ακύρ|ακυρ|καταγγελ|τερματισ)", re.IGNORECASE),
)
_PAYMENT_REQUEST_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"(?:abbuch|lastschrift|sepa|bankkonto|von\s+meinem\s+konto|"
        r"konto\s+(?:belast|einzieh)|zahlung(?:en)?\s+(?:stop|einstell)|"
        r"beitrag(?:e|s)?\s+(?:nicht\s+mehr\s+abbuch|stop))",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:خصم|سحب|اقتطاع|حساب\s+بنكي|تفويض\s+دفع|إيقاف\s+الدفع|"
        r"ايقاف\s+الدفع|لا\s+تخصم|وقف\s+الخصم)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:direct\s+debit|bank\s+account|stop\s+(?:payment|charging)|"
        r"no\s+further\s+(?:payment|charge))",
        re.IGNORECASE,
    ),
    re.compile(r"(?:списан|банківськ.*рахун|припин.*платеж)", re.IGNORECASE),
    re.compile(
        r"(?:πάγια\s+εντολή|παγια\s+εντολη|τραπεζικ.*λογαριασ|διακοπ.*πληρωμ)",
        re.IGNORECASE,
    ),
)
_UNSUPPORTED_PAYMENT_SENTENCE: Final[re.Pattern[str]] = re.compile(
    r"(?:abbuch|lastschrift|sepa|bankkonto|bankverbindung|von\s+meinem\s+konto|"
    r"konto\s+(?:belast|einzieh)|ein(?:gezogen|ziehen)|direct\s+debit|"
    r"bank\s+account|no\s+further\s+(?:payment|charge)|stop\s+(?:payment|charging)|"
    r"خصم|سحب|اقتطاع|حساب\s+بنكي|تفويض\s+دفع|"
    r"списан|банківськ.*рахун|πάγια\s+εντολή|παγια\s+εντολη|τραπεζικ.*λογαριασ)",
    re.IGNORECASE,
)
_CONFIRMATION_VERB_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:bestätig|bestaetig|confirmation|confirm|تأكيد|أكد|підтвер|επιβεβαί|επιβεβαι)",
    re.IGNORECASE,
)
_CONFIRMATION_CONTEXT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:kündig|kuendig|vertrag|vertragsende|eingang|wirksam|end(?:e|datum)|"
    r"cancellation|termination|contract|receipt|effective|end\s+date|"
    r"إلغاء|الغاء|العقد|انتهاء|استلام|تاريخ|"
    r"розірв|договор|отриман|закінчен|дат|"
    r"καταγγελ|σύμβασ|συμβασ|παραλαβ|λήξ|ληξ|ημερομην)",
    re.IGNORECASE,
)
_UNKNOWN_TIMING_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"(?:ما\s+عندي|لا\s+أعرف|لا\s+اعرف|غير\s+معروف|غير\s+مؤكد)"
        r".{0,100}(?:مدة\s+الإلغاء|مهلة\s+الإلغاء|تاريخ\s+نهاية|انتهاء\s+العقد)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:kündigungsfrist|vertragsende|enddatum).{0,70}"
        r"(?:nicht\s+bekannt|unbekannt|keine\s+angabe)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:do\s+not\s+know|don't\s+know|unknown).{0,90}"
        r"(?:notice\s+period|end\s+date|contract\s+end)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:не\s+знаю|невідом).{0,90}(?:строк|дат.*закінчен)", re.IGNORECASE),
    re.compile(r"(?:δεν\s+γνωρίζ|αγνωστ).{0,90}(?:προθεσμ|ημερομην.*λήξ|ημερομην.*ληξ)", re.IGNORECASE),
)
_NEXT_POSSIBLE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"zum\s+nächstmöglichen\s+(?:zeitpunkt|termin)", re.IGNORECASE),
    re.compile(r"(?:في|ب)\s*أقرب\s+موعد\s+ممكن", re.IGNORECASE),
    re.compile(r"(?:at|on)\s+the\s+earliest\s+possible\s+(?:date|time)", re.IGNORECASE),
    re.compile(r"(?:якнайшвидш|найближч).*можлив", re.IGNORECASE),
    re.compile(r"(?:το\s+συντομότερο|το\s+συντομοτερο|νωρίτερ.*δυνατ)", re.IGNORECASE),
)
_EXTRAORDINARY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:außerordentlich|ausserordentlich|fristlos|sonderkündigungsrecht|"
    r"extraordinary\s+termination|إلغاء\s+استثنائي|فسخ\s+فوري|"
    r"позачергов.*розір|έκτακτ.*καταγγελ|εκτακτ.*καταγγελ)",
    re.IGNORECASE,
)

_SAFE_CONFIRMATION: Final[dict[str, str]] = {
    "de": "Bitte bestätigen Sie mir schriftlich den Eingang dieser Kündigung sowie das Datum, zu dem der Vertrag endet.",
    "ar": "يرجى تأكيد استلام طلب الإلغاء كتابيًا، مع ذكر تاريخ انتهاء العقد.",
    "en": "Please confirm in writing that you received this cancellation and the date on which the contract ends.",
    "uk": "Будь ласка, письмово підтвердьте отримання цього повідомлення про розірвання та дату завершення договору.",
    "el": "Παρακαλώ επιβεβαιώστε γραπτώς την παραλαβή της καταγγελίας και την ημερομηνία λήξης της σύμβασης.",
}


@dataclass(frozen=True)
class CancellationDraftGroundingResult:
    applicable: bool
    draft: str
    changed: bool = False
    rejection_reason: str = ""


def _normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        character
        for character in text
        if character in {"\n", "\t"}
        or unicodedata.category(character) not in {"Cf", "Cs"}
    ).strip()


def _selected_language(value: str) -> str:
    return value if value in _SUPPORTED_LANGUAGES else "de"


def _contains_any(value: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    text = _normalize(value)
    return any(pattern.search(text) for pattern in patterns)


def is_cancellation_request(value: str) -> bool:
    text = _normalize(value)
    return bool(text and any(pattern.search(text) for pattern in _CANCELLATION_PATTERNS))


def is_cancellation_draft(value: str) -> bool:
    text = _normalize(value)
    if not text:
        return False
    folded = text.casefold()
    subject_present = any(
        marker in folded
        for marker in ("betreff:", "subject:", "الموضوع:", "тема:", "θέμα:", "θεμα:")
    )
    closing_present = any(
        marker.casefold() in folded
        for marker in (
            "Mit freundlichen Grüßen",
            "Mit freundlichen Gruessen",
            "Kind regards",
            "Sincerely",
            "مع خالص التحية",
            "З повагою",
            "Με εκτίμηση",
            "Με εκτιμηση",
        )
    )
    cancellation_present = any(pattern.search(text) for pattern in _CANCELLATION_PATTERNS)
    return subject_present and closing_present and cancellation_present


def _has_next_possible_wording(value: str) -> bool:
    return _contains_any(value, _NEXT_POSSIBLE_PATTERNS)


def _is_confirmation_sentence(value: str) -> bool:
    text = _normalize(value)
    if not text:
        return False
    if re.match(r"^(?:betreff|subject|الموضوع|тема|θέμα|θεμα)\s*:", text, re.IGNORECASE):
        return False
    return bool(
        _CONFIRMATION_VERB_PATTERN.search(text)
        and _CONFIRMATION_CONTEXT_PATTERN.search(text)
    )


def _detect_draft_language(draft: str, fallback: str) -> str:
    text = _normalize(draft)
    if re.search(r"(?:Sehr\s+geehrte|Mit\s+freundlichen\s+Grüßen|Kündigung)", text, re.IGNORECASE):
        return "de"
    if re.search(r"(?:Dear\s+Sir|Yours\s+sincerely|cancellation|termination)", text, re.IGNORECASE):
        return "en"
    if re.search(r"[\u0600-\u06ff]", text):
        return "ar"
    if re.search(r"[\u0400-\u04ff]", text):
        return "uk"
    if re.search(r"[\u0370-\u03ff]", text):
        return "el"
    return _selected_language(fallback)


def _strong_anchors(value: str) -> tuple[str, ...]:
    text = _normalize(value)
    anchors: set[str] = set()
    anchors.update(match.group(0) for match in _DATE_PATTERN.finditer(text))
    anchors.update(match.group(0) for match in _IDENTIFIER_PATTERN.finditer(text))
    anchors.update(match.group(0) for match in _COMPANY_PATTERN.finditer(text))
    return tuple(sorted(anchors, key=lambda item: (-len(item), item.casefold())))


def _split_sentences(paragraph: str) -> tuple[str, ...]:
    value = paragraph.strip()
    if not value:
        return ()
    return tuple(
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])(?:[ \t]+|\n+)", value)
        if sentence.strip()
    )


def _insert_before_closing(paragraphs: list[str], sentence: str) -> list[str]:
    closing_pattern = re.compile(
        r"^(?:Mit\s+freundlichen\s+Grüßen|Yours\s+sincerely|Kind\s+regards|"
        r"مع\s+خالص\s+التحية|З\s+повагою|Με\s+εκτίμηση|Με\s+εκτιμηση)",
        re.IGNORECASE,
    )
    for index, paragraph in enumerate(paragraphs):
        if closing_pattern.search(paragraph.strip()):
            return paragraphs[:index] + [sentence] + paragraphs[index:]
    return paragraphs + [sentence]


def ground_cancellation_draft(
    request_text: str,
    draft: str,
    *,
    previous_draft: str = "",
    conversation_language: str,
) -> CancellationDraftGroundingResult:
    """Apply a fail-closed post-generation boundary to one cancellation draft."""
    request = _normalize(request_text)
    clean = _normalize(draft)
    baseline = _normalize(previous_draft) if is_cancellation_draft(previous_draft) else ""
    if not is_cancellation_draft(clean):
        return CancellationDraftGroundingResult(applicable=False, draft=clean)

    source_context = "\n".join(part for part in (request, baseline) if part)
    clean_folded = clean.casefold()
    missing_anchors = [
        anchor
        for anchor in _strong_anchors(source_context)
        if anchor.casefold() not in clean_folded
    ]
    if missing_anchors:
        return CancellationDraftGroundingResult(
            applicable=True,
            draft=clean,
            rejection_reason="verified-anchor-missing",
        )

    allowed_dates = {match.group(0) for match in _DATE_PATTERN.finditer(source_context)}
    draft_dates = {match.group(0) for match in _DATE_PATTERN.finditer(clean)}
    if not draft_dates.issubset(allowed_dates):
        return CancellationDraftGroundingResult(
            applicable=True,
            draft=clean,
            rejection_reason="unsupported-date-added",
        )

    timing_unknown = _contains_any(request, _UNKNOWN_TIMING_PATTERNS) or _has_next_possible_wording(baseline)
    if timing_unknown and not _has_next_possible_wording(clean):
        return CancellationDraftGroundingResult(
            applicable=True,
            draft=clean,
            rejection_reason="unknown-timing-not-preserved",
        )

    if _EXTRAORDINARY_PATTERN.search(clean) and not _EXTRAORDINARY_PATTERN.search(request):
        return CancellationDraftGroundingResult(
            applicable=True,
            draft=clean,
            rejection_reason="unsupported-extraordinary-termination",
        )

    payment_allowed = _contains_any(request, _PAYMENT_REQUEST_PATTERNS)
    draft_sentences = [
        sentence
        for paragraph in re.split(r"\n\s*\n+", clean)
        for sentence in _split_sentences(paragraph)
    ]
    confirmation_expected = any(_is_confirmation_sentence(sentence) for sentence in draft_sentences) or any(
        _is_confirmation_sentence(sentence)
        for sentence in _split_sentences(request)
    )
    language = _detect_draft_language(clean, conversation_language)
    safe_confirmation = _SAFE_CONFIRMATION[language]

    grounded: list[str] = []
    confirmation_added = False
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", clean) if part.strip()]
    for paragraph in paragraphs:
        kept_sentences: list[str] = []
        for sentence in _split_sentences(paragraph):
            if _is_confirmation_sentence(sentence):
                if confirmation_expected and not confirmation_added:
                    kept_sentences.append(safe_confirmation)
                    confirmation_added = True
                continue
            if not payment_allowed and _UNSUPPORTED_PAYMENT_SENTENCE.search(sentence):
                continue
            kept_sentences.append(sentence)
        if kept_sentences:
            grounded.append(" ".join(kept_sentences))

    if confirmation_expected and not confirmation_added:
        grounded = _insert_before_closing(grounded, safe_confirmation)
        confirmation_added = True

    result = "\n\n".join(grounded).strip()
    if not result or not is_cancellation_draft(result):
        return CancellationDraftGroundingResult(
            applicable=True,
            draft=clean,
            rejection_reason="grounded-draft-invalid",
        )
    if not payment_allowed and _UNSUPPORTED_PAYMENT_SENTENCE.search(result):
        return CancellationDraftGroundingResult(
            applicable=True,
            draft=clean,
            rejection_reason="unsupported-payment-language-remains",
        )
    if confirmation_expected and result.count(safe_confirmation) != 1:
        return CancellationDraftGroundingResult(
            applicable=True,
            draft=clean,
            rejection_reason="confirmation-normalization-failed",
        )
    if timing_unknown and language == "de" and NEXT_POSSIBLE_DATE_WORDING.casefold() not in result.casefold():
        return CancellationDraftGroundingResult(
            applicable=True,
            draft=clean,
            rejection_reason="next-possible-wording-lost",
        )

    result_folded = result.casefold()
    if any(anchor.casefold() not in result_folded for anchor in _strong_anchors(source_context)):
        return CancellationDraftGroundingResult(
            applicable=True,
            draft=clean,
            rejection_reason="verified-anchor-lost-after-grounding",
        )

    return CancellationDraftGroundingResult(
        applicable=True,
        draft=result,
        changed=result != clean,
    )


def _company(draft: str) -> str:
    match = _COMPANY_PATTERN.search(_normalize(draft))
    return match.group(0) if match else ""


def _reference_number(draft: str) -> str:
    text = _normalize(draft)
    patterns = (
        re.compile(
            r"(?:Vertragsnummer|Vertrag\s+Nr\.?|contract\s+number|رقم\s+العقد)"
            r"\s*[:#-]?\s*([A-Za-z0-9_-]{4,50})",
            re.IGNORECASE,
        ),
        _IDENTIFIER_PATTERN,
    )
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(1) if match.lastindex else match.group(0)
    return ""


def _contract_start_date(draft: str) -> str:
    text = _normalize(draft)
    for match in _DATE_PATTERN.finditer(text):
        window = text[max(0, match.start() - 100):min(len(text), match.end() + 100)].casefold()
        if any(
            marker in window
            for marker in (
                "begonnen",
                "vertragsbeginn",
                "start date",
                "started",
                "تاريخ بدء",
                "بدأ",
                "почат",
                "έναρξ",
                "εναρξ",
            )
        ):
            return match.group(0)
    return ""


def _placeholder_roles(draft: str) -> tuple[str, ...]:
    roles: list[str] = []
    seen: set[str] = set()
    sender_started = False
    for match in _PLACEHOLDER_PATTERN.finditer(_normalize(draft)):
        value = re.sub(r"\s+", " ", match.group(1)).strip().casefold()
        if any(
            marker in value
            for marker in (
                "iban",
                "bic",
                "bankverbindung",
                "bank account",
                "konto",
                "passwort",
                "password",
                "steuer-id",
                "tax id",
            )
        ):
            continue

        role = "other"
        if any(marker in value for marker in ("vor- und nachname", "ihr name", "ihre name", "full name", "الاسم")):
            role = "sender_name"
            sender_started = True
        elif any(marker in value for marker in ("telefon", "phone", "الهاتف", "телефон", "τηλέφων")):
            role = "phone"
            sender_started = True
        elif any(marker in value for marker in ("e-mail", "email", "البريد", "електрон", "ηλεκτρον")):
            role = "email"
            sender_started = True
        elif any(marker in value for marker in ("unterschrift", "signature", "التوقيع", "підпис", "υπογραφ")):
            role = "signature"
            sender_started = True
        elif value in {"datum", "date", "التاريخ", "дата", "ημερομηνία"} or "ort, datum" in value or "ort und datum" in value:
            role = "letter_date"
        elif any(marker in value for marker in ("postleitzahl", "plz", "postal code", "zip code")):
            role = (
                "sender_postal"
                if sender_started or any(marker in value for marker in ("ihr", "ihre", "your"))
                else "recipient_postal"
            )
        elif any(marker in value for marker in ("adresse", "anschrift", "straße", "strasse", "street", "address", "العنوان", "адрес", "διεύθυν")):
            recipient_marker = any(
                marker in value
                for marker in (
                    "anbieter",
                    "empfänger",
                    "empfaenger",
                    "recipient",
                    "provider",
                    "الجهة",
                    "одержувач",
                    "παραλήπτ",
                )
            )
            role = (
                "recipient_address"
                if recipient_marker or (not sender_started and "ihr" not in value and "your" not in value)
                else "sender_address"
            )
            if role == "sender_address":
                sender_started = True

        if role not in seen:
            seen.add(role)
            roles.append(role)
    return tuple(roles)


_FIELD_LABELS: Final[dict[str, dict[str, str]]] = {
    "recipient_address": {
        "ar": "العنوان البريدي للجهة المستلمة (الشارع ورقم المنزل)",
        "de": "Postanschrift des Empfängers (Straße und Hausnummer)",
        "en": "recipient street and house number",
        "uk": "вулиця і номер будинку одержувача",
        "el": "οδός και αριθμός παραλήπτη",
    },
    "recipient_postal": {
        "ar": "الرمز البريدي والمدينة للجهة المستلمة",
        "de": "Postleitzahl und Ort des Empfängers",
        "en": "recipient postal code and city",
        "uk": "поштовий індекс і місто одержувача",
        "el": "ταχυδρομικός κώδικας και πόλη παραλήπτη",
    },
    "sender_name": {
        "ar": "الاسم الكامل",
        "de": "vollständiger Name",
        "en": "full name",
        "uk": "повне ім’я",
        "el": "ονοματεπώνυμο",
    },
    "sender_address": {
        "ar": "عنوانك البريدي (الشارع ورقم المنزل)",
        "de": "deine Straße und Hausnummer",
        "en": "your street and house number",
        "uk": "твоя вулиця і номер будинку",
        "el": "η οδός και ο αριθμός κατοικίας σου",
    },
    "sender_postal": {
        "ar": "الرمز البريدي والمدينة لعنوانك",
        "de": "deine Postleitzahl und dein Ort",
        "en": "your postal code and city",
        "uk": "твій поштовий індекс і місто",
        "el": "ο ταχυδρομικός κώδικας και η πόλη σου",
    },
    "letter_date": {
        "ar": "تاريخ كتابة الرسالة",
        "de": "Datum des Schreibens",
        "en": "letter date",
        "uk": "дата листа",
        "el": "ημερομηνία επιστολής",
    },
    "phone": {
        "ar": "رقم الهاتف",
        "de": "Telefonnummer",
        "en": "phone number",
        "uk": "номер телефону",
        "el": "αριθμός τηλεφώνου",
    },
    "email": {
        "ar": "البريد الإلكتروني",
        "de": "E-Mail-Adresse",
        "en": "e-mail address",
        "uk": "електронна адреса",
        "el": "διεύθυνση e-mail",
    },
    "signature": {
        "ar": "التوقيع عند الإرسال الورقي",
        "de": "Unterschrift beim Postversand",
        "en": "signature for postal sending",
        "uk": "підпис для паперового листа",
        "el": "υπογραφή για ταχυδρομική αποστολή",
    },
    "other": {
        "ar": "حقل إضافي ظاهر في المسودة",
        "de": "weiteres sichtbares Feld",
        "en": "another visible field",
        "uk": "інше видиме поле",
        "el": "άλλο ορατό πεδίο",
    },
}


def _field_labels(draft: str, language: str) -> tuple[str, ...]:
    selected = _selected_language(language)
    return tuple(_FIELD_LABELS[role][selected] for role in _placeholder_roles(draft))


def build_cancellation_companion_summary(
    draft: str,
    *,
    conversation_language: str,
) -> str | None:
    if not is_cancellation_draft(draft):
        return None
    language = _selected_language(conversation_language)
    provider = _company(draft)
    uses_next_possible = _has_next_possible_wording(draft)
    if language == "ar":
        timing = "في أقرب موعد ممكن" if uses_next_possible else "وفق التاريخ المكتوب في المسودة"
        return (
            f"هذه مسودة لإلغاء العقد مع {provider or 'الجهة المذكورة'} {timing}. "
            "تطلب تأكيدًا كتابيًا باستلام الإلغاء وتاريخ انتهاء العقد، ولم تُرسل بعد."
        )
    if language == "en":
        timing = "at the earliest possible date" if uses_next_possible else "on the date stated in the draft"
        return (
            f"This is a cancellation draft for {provider or 'the named provider'} {timing}. "
            "It asks for written confirmation of receipt and the contract end date, and it has not been sent."
        )
    if language == "uk":
        timing = "у найближчу можливу дату" if uses_next_possible else "на дату, зазначену в чернетці"
        return (
            f"Це чернетка розірвання договору із {provider or 'зазначеним постачальником'} {timing}. "
            "Вона просить письмово підтвердити отримання і дату завершення договору; її ще не надіслано."
        )
    if language == "el":
        timing = "το συντομότερο δυνατό" if uses_next_possible else "στην ημερομηνία που αναφέρεται στο προσχέδιο"
        return (
            f"Αυτό είναι προσχέδιο καταγγελίας για {provider or 'τον αναφερόμενο πάροχο'} {timing}. "
            "Ζητά γραπτή επιβεβαίωση παραλαβής και ημερομηνίας λήξης· δεν έχει σταλεί."
        )
    timing = NEXT_POSSIBLE_DATE_WORDING if uses_next_possible else "zu dem im Entwurf genannten Datum"
    return (
        f"Dies ist ein Kündigungsentwurf für {provider or 'den genannten Anbieter'} {timing}. "
        "Er bittet um eine schriftliche Eingangsbestätigung und das Vertragsenddatum und wurde noch nicht versendet."
    )


def build_cancellation_assistance_card(
    draft: str,
    explanation: str,
    *,
    conversation_language: str,
) -> str | None:
    del explanation
    summary = build_cancellation_companion_summary(
        draft,
        conversation_language=conversation_language,
    )
    if summary is None:
        return None
    language = _selected_language(conversation_language)
    labels = _field_labels(draft, language)

    if language == "ar":
        fields = (
            "• أكمل الحقول بين [ ] قبل الإرسال: " + "، ".join(labels) + "."
            if labels
            else "• لا توجد حقول واضحة بين [ ]؛ راجع بيانات المرسل والجهة يدويًا."
        )
        return (
            f"{summary}\n\nقبل الإرسال:\n{fields}\n"
            "• راجع اسم الجهة، رقم العقد، تاريخ بدء العقد، وأي مهلة مؤكدة.\n"
            "• لا ترسل الرسالة قبل أن تفهمها وتراجعها.\n\n"
            "كيف أساعدك الآن؟\n"
            "1️⃣ ترجمة كاملة للعربية للفهم فقط\n"
            "2️⃣ شرح مبسّط للمحتوى\n"
            "3️⃣ مساعدتك في تعبئة الحقول الناقصة\n"
            "4️⃣ خطوات الإرسال والمتابعة\n\n"
            "اكتب رقم الخيار أو الطلب بكلماتك."
        )
    if language == "en":
        fields = (
            "• Complete these bracketed fields before sending: " + ", ".join(labels) + "."
            if labels
            else "• No clear bracketed fields are visible; verify sender and recipient details manually."
        )
        return (
            f"{summary}\n\nBefore sending:\n{fields}\n"
            "• Check the provider, contract number, contract start date, and every verified deadline.\n"
            "• Send the message only after you understand and review it.\n\n"
            "How should I help next?\n"
            "1️⃣ Full English translation for understanding only\n"
            "2️⃣ Plain-language explanation\n"
            "3️⃣ Help completing missing fields\n"
            "4️⃣ Sending and follow-up steps\n\n"
            "Reply with the option number or describe the request."
        )
    if language == "uk":
        fields = (
            "• Перед надсиланням заповни поля в дужках: " + ", ".join(labels) + "."
            if labels
            else "• Чітких полів у дужках немає; перевір дані відправника й одержувача вручну."
        )
        return (
            f"{summary}\n\nПеред надсиланням:\n{fields}\n"
            "• Перевір постачальника, номер договору, дату початку та кожен підтверджений строк.\n"
            "• Надсилай текст лише після того, як зрозумієш і перевіриш його.\n\n"
            "Як допомогти далі?\n"
            "1️⃣ Повний переклад українською лише для розуміння\n"
            "2️⃣ Просте пояснення змісту\n"
            "3️⃣ Допомога із заповненням пропущених полів\n"
            "4️⃣ Кроки надсилання та подальших дій\n\n"
            "Напиши номер варіанта або сформулюй запит своїми словами."
        )
    if language == "el":
        fields = (
            "• Συμπλήρωσε πριν από την αποστολή τα πεδία σε αγκύλες: " + ", ".join(labels) + "."
            if labels
            else "• Δεν φαίνονται σαφή πεδία σε αγκύλες· έλεγξε χειροκίνητα αποστολέα και παραλήπτη."
        )
        return (
            f"{summary}\n\nΠριν από την αποστολή:\n{fields}\n"
            "• Έλεγξε τον πάροχο, τον αριθμό σύμβασης, την ημερομηνία έναρξης και κάθε επιβεβαιωμένη προθεσμία.\n"
            "• Στείλε το κείμενο μόνο αφού το κατανοήσεις και το ελέγξεις.\n\n"
            "Πώς να βοηθήσω στη συνέχεια;\n"
            "1️⃣ Πλήρης μετάφραση στα ελληνικά μόνο για κατανόηση\n"
            "2️⃣ Απλή εξήγηση του περιεχομένου\n"
            "3️⃣ Βοήθεια στη συμπλήρωση ελλιπών πεδίων\n"
            "4️⃣ Βήματα αποστολής και παρακολούθησης\n\n"
            "Γράψε τον αριθμό της επιλογής ή το αίτημα με δικά σου λόγια."
        )

    fields = (
        "• Fülle vor dem Versand diese Felder in eckigen Klammern aus: " + ", ".join(labels) + "."
        if labels
        else "• Es sind keine eindeutigen Felder in eckigen Klammern sichtbar; prüfe Absender und Empfänger manuell."
    )
    return (
        f"{summary}\n\nVor dem Versand:\n{fields}\n"
        "• Prüfe Anbieter, Vertragsnummer, Vertragsbeginn und jede bestätigte Frist.\n"
        "• Versende den Text erst, wenn du ihn verstanden und geprüft hast.\n\n"
        "Wie soll ich weiterhelfen?\n"
        "1️⃣ Vollständige Übersetzung zum Verständnis\n"
        "2️⃣ Einfache Erklärung des Inhalts\n"
        "3️⃣ Hilfe beim Ausfüllen fehlender Felder\n"
        "4️⃣ Versand- und Nachfassschritte\n\n"
        "Schreib die Nummer oder den Wunsch in eigenen Worten."
    )


def build_cancellation_plain_explanation(
    draft: str,
    *,
    conversation_language: str,
) -> str | None:
    if not is_cancellation_draft(draft):
        return None
    language = _selected_language(conversation_language)
    provider = _company(draft)
    reference = _reference_number(draft)
    start_date = _contract_start_date(draft)
    next_possible = _has_next_possible_wording(draft)
    labels = _field_labels(draft, language)

    if language == "ar":
        lines = ["شرح مبسّط للمحتوى:"]
        lines.append(
            f"• تطلب المسودة إلغاء العقد مع {provider or 'الجهة المذكورة'}"
            + (" في أقرب موعد ممكن." if next_possible else ".")
        )
        if reference:
            lines.append(f"• رقم العقد أو المرجع المذكور: {reference}.")
        if start_date:
            lines.append(f"• تاريخ بدء العقد المذكور في النص: {start_date}.")
        lines.append("• تطلب تأكيدًا كتابيًا باستلام الإلغاء وتاريخ انتهاء العقد.")
        if labels:
            lines.append("• الحقول الناقصة مخصّصة لـ: " + "، ".join(labels) + ".")
        lines.append("• تاريخ الرسالة يُكتَب من المستخدم عند تجهيز النسخة النهائية؛ لا يعني تلقائيًا تاريخ اليوم.")
        lines.append("• هذه مسودة للفهم والمراجعة ولم تُرسل، ولا تعني أن العقد أُلغي فعليًا.")
        return "\n".join(lines)

    if language == "en":
        lines = ["Plain-language explanation:"]
        lines.append(
            f"• The draft asks to cancel the contract with {provider or 'the named provider'}"
            + (" at the earliest possible date." if next_possible else ".")
        )
        if reference:
            lines.append(f"• Stated contract or reference number: {reference}.")
        if start_date:
            lines.append(f"• Stated contract start date: {start_date}.")
        lines.append("• It asks for written confirmation of receipt and the contract end date.")
        if labels:
            lines.append("• Missing fields are for: " + ", ".join(labels) + ".")
        lines.append("• The letter date is entered by the user in the final version; it does not automatically mean today's date.")
        lines.append("• This is an unsent draft for review and does not mean the contract has already ended.")
        return "\n".join(lines)

    if language == "uk":
        lines = ["Просте пояснення змісту:"]
        lines.append(
            f"• Чернетка просить розірвати договір із {provider or 'зазначеним постачальником'}"
            + (" у найближчу можливу дату." if next_possible else ".")
        )
        if reference:
            lines.append(f"• Зазначений номер договору або посилання: {reference}.")
        if start_date:
            lines.append(f"• Зазначена дата початку договору: {start_date}.")
        lines.append("• Вона просить письмово підтвердити отримання та дату завершення договору.")
        if labels:
            lines.append("• Поля, які треба заповнити: " + ", ".join(labels) + ".")
        lines.append("• Дату листа користувач вносить у фінальну версію; це не обов’язково сьогоднішня дата.")
        lines.append("• Це ненадіслана чернетка для перевірки; вона не означає, що договір уже завершено.")
        return "\n".join(lines)

    if language == "el":
        lines = ["Απλή εξήγηση του περιεχομένου:"]
        lines.append(
            f"• Το προσχέδιο ζητά την καταγγελία της σύμβασης με {provider or 'τον αναφερόμενο πάροχο'}"
            + (" το συντομότερο δυνατό." if next_possible else ".")
        )
        if reference:
            lines.append(f"• Αναφερόμενος αριθμός σύμβασης ή αναφοράς: {reference}.")
        if start_date:
            lines.append(f"• Αναφερόμενη ημερομηνία έναρξης: {start_date}.")
        lines.append("• Ζητά γραπτή επιβεβαίωση παραλαβής και ημερομηνίας λήξης της σύμβασης.")
        if labels:
            lines.append("• Πεδία που πρέπει να συμπληρωθούν: " + ", ".join(labels) + ".")
        lines.append("• Η ημερομηνία επιστολής συμπληρώνεται από τον χρήστη στην τελική έκδοση· δεν σημαίνει αυτόματα τη σημερινή ημερομηνία.")
        lines.append("• Πρόκειται για μη απεσταλμένο προσχέδιο και δεν σημαίνει ότι η σύμβαση έχει ήδη λήξει.")
        return "\n".join(lines)

    lines = ["Einfache Erklärung des Inhalts:"]
    lines.append(
        f"• Der Entwurf kündigt den Vertrag bei {provider or 'dem genannten Anbieter'}"
        + (f" {NEXT_POSSIBLE_DATE_WORDING}." if next_possible else ".")
    )
    if reference:
        lines.append(f"• Genannte Vertrags- oder Referenznummer: {reference}.")
    if start_date:
        lines.append(f"• Genannter Vertragsbeginn: {start_date}.")
    lines.append("• Er bittet um eine schriftliche Eingangsbestätigung und das Vertragsenddatum.")
    if labels:
        lines.append("• Auszufüllende Felder: " + ", ".join(labels) + ".")
    lines.append("• Das Briefdatum wird in der endgültigen Fassung eingetragen und bedeutet nicht automatisch das heutige Datum.")
    lines.append("• Dies ist ein unversendeter Entwurf und keine Bestätigung, dass der Vertrag bereits beendet ist.")
    return "\n".join(lines)


def build_cancellation_missing_fields_help(
    draft: str,
    *,
    conversation_language: str,
) -> str | None:
    if not is_cancellation_draft(draft):
        return None
    language = _selected_language(conversation_language)
    labels = _field_labels(draft, language)

    if language == "ar":
        if not labels:
            return "لا توجد حقول واضحة بين [ ] في المسودة. راجع مع ذلك بيانات الجهة والمرسل وتاريخ الرسالة يدويًا."
        return (
            "الحقول التي تحتاج مراجعة أو تعبئة:\n"
            + "\n".join(f"• {label}" for label in labels)
            + "\n\nلأعبّيها معك، أرسل القيم غير الحساسة بهذه الصيغة:\n"
            + "\n".join(f"{label}: ..." for label in labels)
            + "\n\nلا ترسل بيانات مصرفية أو كلمات مرور في الدردشة."
        )
    if language == "en":
        if not labels:
            return "No clear bracketed fields are visible. Still verify the recipient, sender, and letter date manually."
        return (
            "Fields to review or complete:\n"
            + "\n".join(f"• {label}" for label in labels)
            + "\n\nTo complete them together, send the non-sensitive values in this format:\n"
            + "\n".join(f"{label}: ..." for label in labels)
            + "\n\nDo not send banking credentials or passwords in chat."
        )
    if language == "uk":
        if not labels:
            return "Чітких полів у дужках немає. Усе одно перевір одержувача, відправника й дату листа вручну."
        return (
            "Поля, які потрібно перевірити або заповнити:\n"
            + "\n".join(f"• {label}" for label in labels)
            + "\n\nЩоб заповнити їх разом, надішли нечутливі значення у такому форматі:\n"
            + "\n".join(f"{label}: ..." for label in labels)
            + "\n\nНе надсилай банківські дані або паролі в чаті."
        )
    if language == "el":
        if not labels:
            return "Δεν φαίνονται σαφή πεδία σε αγκύλες. Έλεγξε παρ’ όλα αυτά χειροκίνητα παραλήπτη, αποστολέα και ημερομηνία επιστολής."
        return (
            "Πεδία που χρειάζονται έλεγχο ή συμπλήρωση:\n"
            + "\n".join(f"• {label}" for label in labels)
            + "\n\nΓια να τα συμπληρώσουμε μαζί, στείλε τις μη ευαίσθητες τιμές με αυτή τη μορφή:\n"
            + "\n".join(f"{label}: ..." for label in labels)
            + "\n\nΜην στέλνεις τραπεζικά στοιχεία ή κωδικούς στη συνομιλία."
        )

    if not labels:
        return "Im Entwurf sind keine eindeutigen Felder in eckigen Klammern sichtbar. Prüfe Empfänger, Absender und Briefdatum trotzdem manuell."
    return (
        "Felder, die geprüft oder ausgefüllt werden müssen:\n"
        + "\n".join(f"• {label}" for label in labels)
        + "\n\nZum gemeinsamen Ausfüllen sende die nicht sensiblen Werte in diesem Format:\n"
        + "\n".join(f"{label}: ..." for label in labels)
        + "\n\nSende keine Bankzugangsdaten oder Passwörter im Chat."
    )
