"""Deterministic grounding for refund, appointment, and contract follow-up drafts.

This module is read-only. It does not call a provider, persist data, send messages,
or execute an external action. It validates structurally valid official drafts against
strong facts supplied in the current request or an existing clean draft and builds
localized, non-legalistic explanations from the final grounded text.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from official_draft_delivery import looks_like_official_draft

_SUPPORTED_LANGUAGES: Final[frozenset[str]] = frozenset({"de", "ar", "en", "uk", "el"})


class OfficialDraftJourney(StrEnum):
    REFUND = "refund"
    APPOINTMENT = "appointment"
    CONTRACT = "contract"


@dataclass(frozen=True)
class JourneyDraftGroundingResult:
    applicable: bool
    journey: OfficialDraftJourney | None
    draft: str
    rejection_reason: str = ""


_DATE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?<!\w)\d{1,2}[./-]\d{1,2}[./-]\d{2,4}(?!\w)"
)
_TIME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?<!\w)(?:[01]?\d|2[0-3]):[0-5]\d(?:\s*Uhr)?(?!\w)",
    re.IGNORECASE,
)
_AMOUNT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?<!\w)\d{1,9}(?:[.,]\d{1,2})?\s?(?:€|EUR|USD|CHF|GBP)(?!\w)",
    re.IGNORECASE,
)
_IDENTIFIER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w@])"
    r"(?=[A-Za-z0-9ÄÖÜäöüß._/-]{4,50}(?![\w@]))"
    r"(?=[A-Za-z0-9ÄÖÜäöüß._/-]*[A-Za-zÄÖÜäöüß])"
    r"(?=[A-Za-z0-9ÄÖÜäöüß._/-]*\d)"
    r"[A-Za-z0-9ÄÖÜäöüß][A-Za-z0-9ÄÖÜäöüß._/-]{3,49}"
    r"(?![\w@])"
)
_COMPANY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9&.'/-]*"
    r"(?:\s+[A-ZÄÖÜ][A-Za-zÄÖÜäöüß0-9&.'/-]*){0,5}\s+"
    r"(?:GmbH|AG|UG(?:\s*\(haftungsbeschränkt\))?|KG|OHG|GbR|e\.V\.)\b"
)
_DURATION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?<!\w)(?:\d+\s*)?(?:"
    r"days?|weeks?|months?|hours?|"
    r"tage?|tagen|wochen?|monate?|monaten|stunden?|"
    r"يومين|يومان|أيام|ايام|يومًا|يوما|يوم|"
    r"أسبوعين|اسبوعين|أسبوعان|اسبوعان|أسابيع|اسابيع|أسبوع|اسبوع|"
    r"شهرين|شهران|أشهر|اشهر|شهر|ساعتين|ساعتان|ساعات|ساعة|"
    r"днів|дні|день|тижнів|тижні|тиждень|місяців|місяці|місяць|годин|години|година|"
    r"ημέρες|ημερες|ημέρα|ημερα|εβδομάδες|εβδομαδες|εβδομάδα|εβδομαδα|"
    r"μήνες|μηνες|μήνας|μηνας|ώρες|ωρες|ώρα|ωρα"
    r")(?!\w)",
    re.IGNORECASE,
)
_LABELLED_REFERENCE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:Kunden(?:nummer|-Nr\.)|Vertrags(?:nummer|-Nr\.)|Bestell(?:nummer|-Nr\.)|"
    r"Buchungs(?:nummer|-Nr\.)|Termin(?:nummer|-Nr\.)|Aktenzeichen|Referenz|"
    r"customer\s+number|contract\s+number|order\s+number|booking\s+reference|reference|"
    r"رقم\s+(?:العميل|العقد|الطلب|الحجز|المرجع)|"
    r"номер\s+(?:клієнта|договору|замовлення|бронювання)|"
    r"αριθμ(?:ός|ος)\s+(?:πελάτη|πελατη|σύμβασης|συμβασης|παραγγελίας|παραγγελιας|κράτησης|κρατησης))"
    r"\s*[:#-]?\s*([A-Za-z0-9._/-]{3,50})",
    re.IGNORECASE,
)
_LABELLED_LOCATION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?im)^\s*(?:Ort|Adresse|Standort|location|address|مكان|العنوان|"
    r"місце|адреса|τοποθεσία|τοποθεσια|διεύθυνση|διευθυνση)\s*:\s*([^\n]{3,140})$"
)
_LABELLED_PARTY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?im)^\s*(?:Anbieter|Händler|Haendler|Verkäufer|Verkaeufer|Empfänger|Empfaenger|"
    r"Vertragspartner|Organisator|Praxis|Behörde|Behoerde|merchant|provider|recipient|"
    r"organizer|clinic|authority|الجهة|الشركة|البائع|المنظم|العيادة|الدائرة|"
    r"постачальник|продавець|організатор|клініка|орган|"
    r"πάροχος|παροχος|πωλητής|πωλητης|διοργανωτής|διοργανωτης|κλινική|κλινικη|αρχή|αρχη)"
    r"\s*:\s*([^\n]{2,120})$"
)

_REFUND_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?:rückerstatt|rueckerstatt|rückzahl|rueckzahl|erstattung|geld\s+zurück|geld\s+zurueck)", re.IGNORECASE),
    re.compile(r"(?:استرداد|استرجاع|إرجاع\s+المبلغ|ارجاع\s+المبلغ|إعادة\s+المبلغ|اعادة\s+المبلغ|تعويض)", re.IGNORECASE),
    re.compile(r"\b(?:refund|reimbursement|money\s+back|repayment)\b", re.IGNORECASE),
    re.compile(r"(?:повернен|відшкодуван)", re.IGNORECASE),
    re.compile(r"(?:επιστροφ\w*\s+χρημάτων|επιστροφ\w*\s+χρηματων|αποζημίω|αποζημιω)", re.IGNORECASE),
)
_APPOINTMENT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?:Termin|Sprechstunde|Vorsprache|Buchung).{0,90}(?:verschieb|verleg|absag|stornier|bestätig|bestaetig|änder|aender)|(?:verschieb|verleg|absag|stornier|bestätig|bestaetig|änder|aender).{0,90}(?:Termin|Sprechstunde|Vorsprache|Buchung)", re.IGNORECASE),
    re.compile(r"(?:موعد|حجز).{0,90}(?:تأجيل|تاجيل|تغيير|إلغاء|الغاء|تأكيد|تاكيد)|(?:تأجيل|تاجيل|تغيير|إلغاء|الغاء|تأكيد|تاكيد).{0,90}(?:الموعد|موعدي|موعدنا|الحجز|حجزي)", re.IGNORECASE),
    re.compile(r"(?:appointment|booking).{0,90}(?:reschedul|move|cancel|confirm|change)|(?:reschedul|move|cancel|confirm|change).{0,90}(?:appointment|booking)", re.IGNORECASE),
    re.compile(r"(?:зустріч|прийом|бронювання).{0,90}(?:перенес|скасув|підтвер|змін)", re.IGNORECASE),
    re.compile(r"(?:ραντεβού|ραντεβου|κράτηση|κρατηση).{0,90}(?:μεταθέ|μεταθε|ακύρ|ακυρ|επιβεβαι|αλλάξ|αλλαξ)", re.IGNORECASE),
)
_CONTRACT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?:Vertrag|Vertragsklausel|Klausel|Laufzeit|Verlängerung|Verlaengerung).{0,120}(?:Rückfrage|Rueckfrage|Klärung|Klaerung|Bestätigung|Bestaetigung|Auskunft|erklär|erklaer)|(?:Rückfrage|Rueckfrage|Klärung|Klaerung|Bestätigung|Bestaetigung|Auskunft).{0,120}(?:Vertrag|Klausel)", re.IGNORECASE),
    re.compile(r"(?:عقد|بند|مدة\s+العقد|تجديد).{0,120}(?:استفسار|توضيح|تأكيد|تاكيد|شرح)|(?:استفسار|توضيح|تأكيد|تاكيد).{0,120}(?:عقد|بند)", re.IGNORECASE),
    re.compile(r"(?:contract|clause|renewal|term).{0,120}(?:clarif|confirm|question|explain)|(?:clarif|confirm|question).{0,120}(?:contract|clause)", re.IGNORECASE),
    re.compile(r"(?:договір|пункт|продовжен).{0,120}(?:уточнен|пояснен|підтвер)", re.IGNORECASE),
    re.compile(r"(?:σύμβασ|συμβασ|ρήτρα|ρητρα|ανανέω|ανανεω).{0,120}(?:διευκρίν|διευκριν|επιβεβαι|εξήγη|εξηγη)", re.IGNORECASE),
)
_CANCELLATION_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?:kündig|kuendig|إلغاء|الغاء|فسخ|\bcancel(?:lation)?\b|\bterminat(?:e|ion)\b|скасув|розірв|ακύρ|ακυρ|καταγγελ)",
    re.IGNORECASE,
)

_REFUND_UNSUPPORTED: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?:garantier\w*|garantie|sicher\s+(?:zurück|zurueck|erstattet)|gesetzlich\w*\s+anspruch|rechtlich\w*\s+anspruch|gesetzlich\w*\s+verpflichtet|chargeback\s+(?:wird|ist)\s+(?:erfolgreich|approved)|refund\s+(?:is|will\s+be)\s+(?:guaranteed|approved)|مضمون|مؤكد\s+استرداد|حق\s+قانوني\s+مؤكد|ملزم\s+قانونيًا|ملزم\s+قانونيا|гарантован|εγγυημέν|εγγυημεν)", re.IGNORECASE),
    re.compile(r"(?:Rückerstattung|Rueckerstattung|refund|الاسترداد|повернення|επιστροφή|επιστροφη)\s+(?:wurde|ist|has\s+been|تم|було|έχει|εχει)\s+(?:genehmigt|akzeptiert|ausgezahlt|approved|accepted|paid|قبول|مقبول|دفع|схвалено|виплачено|εγκρίθηκε|εγκριθηκε|καταβλήθηκε|καταβληθηκε)", re.IGNORECASE),
    re.compile(r"(?:Klage|Anzeige|Inkasso|Gericht|lawsuit|police\s+report|debt\s+collection|دعوى|شرطة|تحصيل\s+ديون|суд|поліці|δικαστ|αστυνομ)", re.IGNORECASE),
)
_APPOINTMENT_UNSUPPORTED: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?:Ihr|Der|Mein)\s+(?:Termin|appointment)\s+(?:wurde|ist)\s+(?:verschoben|verlegt|abgesagt|storniert|bestätigt|bestaetigt|gebucht|rescheduled|moved|cancelled|canceled|confirmed|booked)", re.IGNORECASE),
    re.compile(r"(?:تم|صار)\s+(?:تأجيل|تاجيل|تغيير|إلغاء|الغاء|تأكيد|تاكيد|حجز)\s+(?:الموعد|موعدك)|(?:الموعد|موعدك)\s+(?:تم|صار)\s+(?:تأجيله|تاجيله|تغييره|إلغاؤه|الغاؤه|تأكيده|تاكيده|حجزه)", re.IGNORECASE),
    re.compile(r"(?:зустріч|прийом).{0,25}(?:було|вже).{0,25}(?:перенесено|скасовано|підтверджено|заброньовано)", re.IGNORECASE),
    re.compile(r"(?:το\s+ραντεβού|το\s+ραντεβου).{0,25}(?:μεταφέρθηκε|μεταφερθηκε|ακυρώθηκε|ακυρωθηκε|επιβεβαιώθηκε|επιβεβαιωθηκε|κλείστηκε|κλειστηκε)", re.IGNORECASE),
)
_CONTRACT_UNSUPPORTED: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?:der\s+Vertrag|die\s+Klausel).{0,35}(?:ist|sei)\s+(?:unwirksam|nichtig|rechtswidrig|unzulässig|unzulaessig|rechtlich\s+nicht\s+bindend|durchsetzbar|nicht\s+durchsetzbar)", re.IGNORECASE),
    re.compile(r"(?:the\s+contract|the\s+clause).{0,35}(?:is|must\s+be)\s+(?:invalid|void|illegal|unenforceable|enforceable|legally\s+binding)", re.IGNORECASE),
    re.compile(r"(?:العقد|البند).{0,35}(?:باطل|غير\s+قانوني|غير\s+ملزم|ملزم\s+قانونيًا|ملزم\s+قانونيا)", re.IGNORECASE),
    re.compile(r"(?:договір|пункт).{0,35}(?:недійсн|незаконн|непідлягає\s+виконанню|юридично\s+обов'язков)", re.IGNORECASE),
    re.compile(r"(?:η\s+σύμβαση|η\s+συμβαση|η\s+ρήτρα|η\s+ρητρα).{0,35}(?:άκυρ|ακυρ|παράνομ|παρανομ|μη\s+εκτελεστ|νομικά\s+δεσμευτικ)", re.IGNORECASE),
    re.compile(r"(?:gesetzlich\s+verpflichtet|eindeutiger\s+rechtlicher\s+Anspruch|definitiv\s+berechtigt|legally\s+required|definite\s+legal\s+right|ملزم\s+قانونيًا|حق\s+قانوني\s+مؤكد|юридично\s+зобов'язан|σαφές\s+νομικό\s+δικαίωμα|σαφες\s+νομικο\s+δικαιωμα)", re.IGNORECASE),
)


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


def _contains(value: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    text = _normalize(value)
    return any(pattern.search(text) for pattern in patterns)


def classify_official_draft_journey(
    request_text: str,
    *,
    previous_draft: str = "",
) -> OfficialDraftJourney | None:
    """Classify only high-confidence non-cancellation official-draft journeys."""
    request = _normalize(request_text)
    previous = _normalize(previous_draft)
    combined = "\n".join(part for part in (request, previous) if part)
    if not combined:
        return None
    if _contains(combined, _REFUND_PATTERNS):
        return OfficialDraftJourney.REFUND
    if _contains(combined, _APPOINTMENT_PATTERNS):
        return OfficialDraftJourney.APPOINTMENT
    if _CANCELLATION_PATTERN.search(combined):
        return None
    if _contains(combined, _CONTRACT_PATTERNS):
        return OfficialDraftJourney.CONTRACT
    return None


def _anchors(value: str, pattern: re.Pattern[str]) -> frozenset[str]:
    return frozenset(match.group(0).strip().casefold() for match in pattern.finditer(_normalize(value)))


def _references(value: str) -> frozenset[str]:
    text = _normalize(value)
    values = {match.group(0).strip().casefold() for match in _IDENTIFIER_PATTERN.finditer(text)}
    values.update(match.group(1).strip().casefold() for match in _LABELLED_REFERENCE_PATTERN.finditer(text))
    return frozenset(values)


def _labelled_values(value: str, pattern: re.Pattern[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    results: list[str] = []
    for match in pattern.finditer(_normalize(value)):
        item = re.sub(r"\s+", " ", match.group(1)).strip()
        key = item.casefold()
        if key and key not in seen:
            seen.add(key)
            results.append(item)
    return tuple(results)


def _unsupported_added(
    draft: str,
    source: str,
    patterns: tuple[re.Pattern[str], ...],
) -> bool:
    return _contains(draft, patterns) and not _contains(source, patterns)


def ground_official_journey_draft(
    request_text: str,
    draft: str,
    *,
    previous_draft: str = "",
    conversation_language: str = "de",
) -> JourneyDraftGroundingResult:
    """Validate one structural draft against strong supplied facts, fail closed on drift."""
    clean = _normalize(draft)
    request = _normalize(request_text)
    baseline = _normalize(previous_draft) if looks_like_official_draft(previous_draft) else ""
    journey = classify_official_draft_journey(request, previous_draft=baseline)
    if journey is None or not looks_like_official_draft(clean):
        return JourneyDraftGroundingResult(False, journey, clean)

    source = "\n".join(part for part in (request, baseline) if part)
    draft_folded = clean.casefold()

    source_companies = _anchors(source, _COMPANY_PATTERN)
    draft_companies = _anchors(clean, _COMPANY_PATTERN)
    source_refs = _references(source)
    draft_refs = _references(clean)
    source_dates = _anchors(source, _DATE_PATTERN)
    draft_dates = _anchors(clean, _DATE_PATTERN)
    source_times = _anchors(source, _TIME_PATTERN)
    draft_times = _anchors(clean, _TIME_PATTERN)
    source_amounts = _anchors(source, _AMOUNT_PATTERN)
    draft_amounts = _anchors(clean, _AMOUNT_PATTERN)
    source_duration_matches = tuple(_DURATION_PATTERN.finditer(source))
    draft_duration_matches = tuple(_DURATION_PATTERN.finditer(clean))
    source_duration_numbers = {
        number
        for match in source_duration_matches
        if (number_match := re.search(r"\d+", match.group(0)))
        for number in (number_match.group(0),)
    }
    draft_duration_numbers = {
        number
        for match in draft_duration_matches
        if (number_match := re.search(r"\d+", match.group(0)))
        for number in (number_match.group(0),)
    }

    output_only = (
        (draft_companies - source_companies)
        or (draft_refs - source_refs)
        or (draft_dates - source_dates)
        or (draft_times - source_times)
        or (draft_amounts - source_amounts)
        or (bool(draft_duration_matches) and not source_duration_matches)
        or (draft_duration_numbers - source_duration_numbers)
    )
    if output_only:
        return JourneyDraftGroundingResult(True, journey, clean, "unsupported-anchor-added")

    required: set[str] = set(
        source_companies
        | source_refs
        | source_dates
        | source_times
        | source_amounts
    )
    missing = [anchor for anchor in required if anchor not in draft_folded]
    if missing:
        return JourneyDraftGroundingResult(True, journey, clean, "verified-anchor-missing")

    labelled_values: tuple[str, ...] = ()
    if journey is OfficialDraftJourney.APPOINTMENT:
        labelled_values = _labelled_values(source, _LABELLED_LOCATION_PATTERN)
    for value in _labelled_values(source, _LABELLED_PARTY_PATTERN) + labelled_values:
        if value.casefold() not in draft_folded:
            return JourneyDraftGroundingResult(True, journey, clean, "verified-labelled-fact-missing")

    unsupported_patterns = {
        OfficialDraftJourney.REFUND: _REFUND_UNSUPPORTED,
        OfficialDraftJourney.APPOINTMENT: _APPOINTMENT_UNSUPPORTED,
        OfficialDraftJourney.CONTRACT: _CONTRACT_UNSUPPORTED,
    }[journey]
    if _unsupported_added(clean, source, unsupported_patterns):
        return JourneyDraftGroundingResult(True, journey, clean, "unsupported-journey-claim")

    return JourneyDraftGroundingResult(True, journey, clean)


def _first_company(value: str) -> str:
    match = _COMPANY_PATTERN.search(_normalize(value))
    return match.group(0) if match else ""


def _first_party(value: str) -> str:
    company = _first_company(value)
    if company:
        return company
    for line in _normalize(value).splitlines():
        candidate = line.strip()
        folded = candidate.casefold()
        if not candidate or candidate.startswith("["):
            continue
        if re.match(r"^(?:betreff|subject|الموضوع|тема|θέμα|θεμα)\s*:", candidate, re.IGNORECASE):
            break
        if folded in {"kundenservice", "customer service", "service", "خدمة العملاء"}:
            continue
        if len(candidate) <= 100 and not re.search(r"\d", candidate):
            return candidate
    return ""


def _first_amount(value: str) -> str:
    match = _AMOUNT_PATTERN.search(_normalize(value))
    return match.group(0) if match else ""


def _first_date(value: str) -> str:
    match = _DATE_PATTERN.search(_normalize(value))
    return match.group(0) if match else ""


def _first_time(value: str) -> str:
    match = _TIME_PATTERN.search(_normalize(value))
    return match.group(0) if match else ""


def build_journey_companion_summary(
    draft: str,
    *,
    journey: OfficialDraftJourney,
    conversation_language: str,
) -> str | None:
    """Build a localized purpose summary from the final validated draft only."""
    clean = _normalize(draft)
    if not looks_like_official_draft(clean):
        return None
    language = _selected_language(conversation_language)
    company = _first_party(clean)
    amount = _first_amount(clean)
    date_value = _first_date(clean)
    time_value = _first_time(clean)
    if language != "de":
        time_value = re.sub(r"\s*Uhr$", "", time_value, flags=re.IGNORECASE)

    if journey is OfficialDraftJourney.REFUND:
        values = {
            "ar": f"هذه مسودة تطلب مراجعة استرداد{f' بقيمة {amount}' if amount else ''} من {company or 'الجهة المذكورة'}. لم تُرسل ولا تضمن استرداد المال.",
            "de": f"Dieser Entwurf bittet {company or 'den genannten Anbieter'} um Prüfung einer Rückerstattung{f' über {amount}' if amount else ''}. Er wurde nicht versendet und garantiert keine Erstattung.",
            "en": f"This draft asks {company or 'the named provider'} to review a refund{f' of {amount}' if amount else ''}. It has not been sent and does not guarantee repayment.",
            "uk": f"Ця чернетка просить {company or 'зазначеного постачальника'} розглянути повернення коштів{f' у сумі {amount}' if amount else ''}. Її не надіслано, і вона не гарантує повернення.",
            "el": f"Αυτό το προσχέδιο ζητά από {company or 'τον αναφερόμενο πάροχο'} να εξετάσει επιστροφή χρημάτων{f' ύψους {amount}' if amount else ''}. Δεν έχει σταλεί και δεν εγγυάται επιστροφή.",
        }
        return values[language]

    if journey is OfficialDraftJourney.APPOINTMENT:
        when = " ".join(part for part in (date_value, time_value) if part)
        values = {
            "ar": f"هذه مسودة مراسلة بخصوص موعد{f' بتاريخ/وقت {when}' if when else ''} لدى {company or 'الجهة المذكورة'}. لم تُرسل ولا تعني أن الموعد حُجز أو تغيّر أو أُلغي بالفعل.",
            "de": f"Dieser Entwurf betrifft einen Termin{f' am {when}' if when else ''} bei {company or 'der genannten Stelle'}. Er wurde nicht versendet und bedeutet nicht, dass der Termin bereits gebucht, geändert oder abgesagt wurde.",
            "en": f"This draft concerns an appointment{f' at {when}' if when else ''} with {company or 'the named organization'}. It has not been sent and does not mean the appointment was already booked, changed, or cancelled.",
            "uk": f"Ця чернетка стосується прийому{f' {when}' if when else ''} у {company or 'зазначеній установі'}. Її не надіслано, і вона не означає, що прийом уже заброньовано, змінено чи скасовано.",
            "el": f"Αυτό το προσχέδιο αφορά ραντεβού{f' στις {when}' if when else ''} με {company or 'τον αναφερόμενο φορέα'}. Δεν έχει σταλεί και δεν σημαίνει ότι το ραντεβού έχει ήδη κλειστεί, αλλάξει ή ακυρωθεί.",
        }
        return values[language]

    values = {
        "ar": f"هذه مسودة تطلب توضيحًا كتابيًا بخصوص عقد أو بند من {company or 'الجهة المذكورة'}. لم تُرسل، ولا تحكم على صحة العقد أو قوته القانونية.",
        "de": f"Dieser Entwurf bittet {company or 'die genannte Vertragspartei'} um schriftliche Klärung zu einem Vertrag oder einer Klausel. Er wurde nicht versendet und bewertet nicht die rechtliche Wirksamkeit des Vertrags.",
        "en": f"This draft asks {company or 'the named contract party'} for written clarification about a contract or clause. It has not been sent and does not decide the contract's legal validity or enforceability.",
        "uk": f"Ця чернетка просить {company or 'зазначену сторону договору'} письмово роз’яснити договір або пункт. Її не надіслано, і вона не визначає юридичну дійсність чи виконуваність договору.",
        "el": f"Αυτό το προσχέδιο ζητά από {company or 'το αναφερόμενο συμβαλλόμενο μέρος'} γραπτή διευκρίνιση για σύμβαση ή ρήτρα. Δεν έχει σταλεί και δεν κρίνει τη νομική ισχύ ή εκτελεστότητα της σύμβασης.",
    }
    return values[language]


def build_journey_plain_explanation(
    draft: str,
    *,
    journey: OfficialDraftJourney,
    conversation_language: str,
) -> str | None:
    """Return deterministic option-2 help without model interpretation."""
    summary = build_journey_companion_summary(
        draft,
        journey=journey,
        conversation_language=conversation_language,
    )
    if summary is None:
        return None
    language = _selected_language(conversation_language)
    headings = {
        "ar": "شرح مبسّط للمحتوى:",
        "de": "Einfache Erklärung des Inhalts:",
        "en": "Plain-language explanation:",
        "uk": "Просте пояснення змісту:",
        "el": "Απλή εξήγηση του περιεχομένου:",
    }
    checks = {
        OfficialDraftJourney.REFUND: {
            "ar": "• راجع الجهة والمبلغ والمرجع والسبب قبل الإرسال.\n• النتيجة تعتمد على رد الجهة ولا توجد ضمانة للاسترداد.",
            "de": "• Prüfe Anbieter, Betrag, Referenz und Begründung vor dem Versand.\n• Das Ergebnis hängt von der Antwort des Anbieters ab; eine Erstattung ist nicht garantiert.",
            "en": "• Check the provider, amount, reference, and reason before sending.\n• The result depends on the provider's response; repayment is not guaranteed.",
            "uk": "• Перевір постачальника, суму, номер і причину перед надсиланням.\n• Результат залежить від відповіді постачальника; повернення не гарантоване.",
            "el": "• Έλεγξε πάροχο, ποσό, αναφορά και αιτιολογία πριν από την αποστολή.\n• Το αποτέλεσμα εξαρτάται από την απάντηση του παρόχου· η επιστροφή δεν είναι εγγυημένη.",
        },
        OfficialDraftJourney.APPOINTMENT: {
            "ar": "• راجع التاريخ والوقت والمكان أو وسيلة التواصل.\n• انتظر تأكيد الجهة؛ المسودة وحدها لا تغيّر الموعد.",
            "de": "• Prüfe Datum, Uhrzeit, Ort oder Kontaktweg.\n• Warte auf die Bestätigung der Stelle; der Entwurf allein ändert den Termin nicht.",
            "en": "• Check the date, time, place, or contact channel.\n• Wait for the organization's confirmation; the draft alone does not change the appointment.",
            "uk": "• Перевір дату, час, місце або канал зв’язку.\n• Дочекайся підтвердження установи; сама чернетка не змінює прийом.",
            "el": "• Έλεγξε ημερομηνία, ώρα, τόπο ή κανάλι επικοινωνίας.\n• Περίμενε επιβεβαίωση του φορέα· το προσχέδιο μόνο του δεν αλλάζει το ραντεβού.",
        },
        OfficialDraftJourney.CONTRACT: {
            "ar": "• الرسالة تطلب توضيحًا أو تأكيدًا، ولا تصدر حكمًا قانونيًا.\n• راجع رقم العقد والبند والمبلغ أو التاريخ المذكور قبل الإرسال.",
            "de": "• Das Schreiben bittet um Klärung oder Bestätigung und trifft keine Rechtsentscheidung.\n• Prüfe Vertragsnummer, Klausel, Betrag oder Datum vor dem Versand.",
            "en": "• The message asks for clarification or confirmation and makes no legal determination.\n• Check the contract reference, clause, amount, or date before sending.",
            "uk": "• Лист просить роз’яснення або підтвердження й не робить юридичного висновку.\n• Перевір номер договору, пункт, суму або дату перед надсиланням.",
            "el": "• Το μήνυμα ζητά διευκρίνιση ή επιβεβαίωση και δεν διατυπώνει νομική κρίση.\n• Έλεγξε αναφορά σύμβασης, ρήτρα, ποσό ή ημερομηνία πριν από την αποστολή.",
        },
    }
    return f"{headings[language]}\n\n{summary}\n\n{checks[journey][language]}"


def build_grounding_failure_message(
    *,
    journey: OfficialDraftJourney,
    conversation_language: str,
) -> str:
    """Return a localized handled response without exposing rejected generated text."""
    language = _selected_language(conversation_language)
    subject = {
        OfficialDraftJourney.REFUND: {
            "ar": "طلب الاسترداد", "de": "Rückerstattungsanfrage", "en": "refund request",
            "uk": "запит на повернення", "el": "αίτημα επιστροφής",
        },
        OfficialDraftJourney.APPOINTMENT: {
            "ar": "رسالة الموعد", "de": "Terminnachricht", "en": "appointment message",
            "uk": "повідомлення про прийом", "el": "μήνυμα ραντεβού",
        },
        OfficialDraftJourney.CONTRACT: {
            "ar": "رسالة العقد", "de": "Vertragsnachricht", "en": "contract message",
            "uk": "повідомлення щодо договору", "el": "μήνυμα σύμβασης",
        },
    }[journey][language]
    templates = {
        "ar": f"لم أرسل {subject}. أوقفت المسودة لأن النص المولّد أضاف أو فقد معلومة مهمة مقارنة بطلبك. أرسل البيانات الأساسية مرة ثانية بشكل واضح وسأجهّز نسخة جديدة آمنة.",
        "de": f"Ich habe die {subject} nicht versendet. Der Entwurf wurde gestoppt, weil der erzeugte Text gegenüber deinen Angaben eine wichtige Information ergänzt oder verloren hat. Sende die Kerndaten bitte noch einmal klar.",
        "en": f"I did not send the {subject}. I stopped the draft because the generated text added or lost an important fact compared with your request. Please send the key details clearly again.",
        "uk": f"Я не надсилав {subject}. Чернетку зупинено, бо згенерований текст додав або втратив важливий факт порівняно з твоїм запитом. Надішли основні дані ще раз чітко.",
        "el": f"Δεν έστειλα το {subject}. Το προσχέδιο σταμάτησε επειδή το παραγόμενο κείμενο πρόσθεσε ή έχασε σημαντικό στοιχείο σε σχέση με το αίτημά σου. Στείλε ξανά καθαρά τα βασικά στοιχεία.",
    }
    return templates[language]
