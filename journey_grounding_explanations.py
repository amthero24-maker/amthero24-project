"""Deterministic localized explanations for grounded official-draft journeys."""
from __future__ import annotations

from journey_draft_grounding import (
    _draft_header_names,
    _extract_anchors,
    _fold,
    _selected_language,
)
from journey_grounding_policy import classify_journey_draft
from journey_grounding_patterns import (
    JOURNEY_APPOINTMENT,
    JOURNEY_CONTRACT,
    JOURNEY_REFUND,
)

def _anchor_values_by_prefix(draft: str, prefix: str) -> tuple[str, ...]:
    anchors = _extract_anchors(draft)
    return tuple(display for key, display in anchors.items() if key.startswith(prefix))


def _primary_party(draft: str) -> str:
    header = tuple(_draft_header_names(draft).values())
    if header:
        return header[0]
    companies = _anchor_values_by_prefix(draft, "name:")
    for value in companies:
        if any(suffix in value for suffix in ("GmbH", " AG", " KG", "OHG", "GbR", "e.V.", "UG")):
            return value
    return companies[0] if companies else ""


def _appointment_action(draft: str, language: str) -> str:
    text = _fold(draft)
    if any(marker in text for marker in ("verschieb", "verleg", "reschedul", "تأجيل", "تغيير الموعد", "перенес", "μεταφορ")):
        return {
            "ar": "تغيير أو تأجيل", "de": "die Verschiebung", "en": "rescheduling",
            "uk": "перенесення", "el": "τη μεταφορά",
        }[language]
    if any(marker in text for marker in ("absag", "stornier", "cancel", "إلغاء الموعد", "الغاء الموعد", "скасув", "ακυρ")):
        return {
            "ar": "إلغاء", "de": "die Absage", "en": "cancellation",
            "uk": "скасування", "el": "την ακύρωση",
        }[language]
    return {
        "ar": "تأكيد أو توضيح", "de": "eine Bestätigung oder Klärung", "en": "confirmation or clarification",
        "uk": "підтвердження або уточнення", "el": "επιβεβαίωση ή διευκρίνιση",
    }[language]


def build_journey_companion_summary(
    draft: str,
    *,
    conversation_language: str,
) -> str | None:
    journey = classify_journey_draft(draft)
    if journey is None:
        return None
    language = _selected_language(conversation_language)
    party = _primary_party(draft)
    party_suffix = {
        "ar": f" مع {party}" if party else "",
        "de": f" an {party}" if party else "",
        "en": f" to {party}" if party else "",
        "uk": f" для {party}" if party else "",
        "el": f" προς {party}" if party else "",
    }[language]

    if journey == JOURNEY_REFUND:
        return {
            "ar": f"هذه مسودة لطلب مراجعة استرداد أو تعويض{party_suffix}. لم تُرسل، ولا تعني أن الاسترداد تمت الموافقة عليه أو دفعه.",
            "de": f"Dieser Entwurf bittet um Prüfung einer Erstattung{party_suffix}. Er wurde nicht versendet und bedeutet nicht, dass eine Erstattung genehmigt oder gezahlt wurde.",
            "en": f"This draft requests review of a refund or reimbursement{party_suffix}. It was not sent and does not mean a refund was approved or paid.",
            "uk": f"Ця чернетка просить розглянути повернення коштів або відшкодування{party_suffix}. Її не надіслано, і вона не означає, що виплату схвалено чи здійснено.",
            "el": f"Αυτό το προσχέδιο ζητά εξέταση επιστροφής χρημάτων ή αποζημίωσης{party_suffix}. Δεν έχει σταλεί και δεν σημαίνει ότι εγκρίθηκε ή πληρώθηκε επιστροφή.",
        }[language]
    if journey == JOURNEY_APPOINTMENT:
        action = _appointment_action(draft, language)
        return {
            "ar": f"هذه مسودة لطلب {action} الموعد{party_suffix}. لم تُرسل، ولا تعني أن الموعد حُجز أو تغيّر أو أُلغي فعليًا.",
            "de": f"Dieser Entwurf bittet um {action} eines Termins{party_suffix}. Er wurde nicht versendet und bedeutet nicht, dass der Termin bereits gebucht, geändert oder abgesagt wurde.",
            "en": f"This draft requests {action} of an appointment{party_suffix}. It was not sent and does not mean the appointment was already booked, changed, or cancelled.",
            "uk": f"Ця чернетка просить про {action} зустрічі{party_suffix}. Її не надіслано, і вона не означає, що зустріч уже заброньовано, змінено чи скасовано.",
            "el": f"Αυτό το προσχέδιο ζητά {action} ραντεβού{party_suffix}. Δεν έχει σταλεί και δεν σημαίνει ότι το ραντεβού έχει ήδη κλειστεί, αλλάξει ή ακυρωθεί.",
        }[language]
    return {
        "ar": f"هذه مسودة لطلب توضيح أو تأكيد كتابي بشأن العقد{party_suffix}. لم تُرسل، ولا تتضمن حكمًا قانونيًا نهائيًا.",
        "de": f"Dieser Entwurf bittet um schriftliche Klärung oder Bestätigung zu einem Vertrag{party_suffix}. Er wurde nicht versendet und enthält keine abschließende rechtliche Bewertung.",
        "en": f"This draft requests written clarification or confirmation about a contract{party_suffix}. It was not sent and does not provide a final legal judgment.",
        "uk": f"Ця чернетка просить письмове уточнення або підтвердження щодо договору{party_suffix}. Її не надіслано, і вона не містить остаточного правового висновку.",
        "el": f"Αυτό το προσχέδιο ζητά γραπτή διευκρίνιση ή επιβεβαίωση για σύμβαση{party_suffix}. Δεν έχει σταλεί και δεν περιέχει οριστική νομική κρίση.",
    }[language]


def _facts_line(draft: str, language: str) -> str:
    amounts = _anchor_values_by_prefix(draft, "amount:")
    dates = _anchor_values_by_prefix(draft, "date:")
    times = _anchor_values_by_prefix(draft, "time:")
    identifiers = _anchor_values_by_prefix(draft, "id:")
    parts: list[str] = []
    labels = {
        "ar": ("المبالغ", "التواريخ", "الأوقات", "المراجع"),
        "de": ("Beträge", "Daten", "Uhrzeiten", "Referenzen"),
        "en": ("amounts", "dates", "times", "references"),
        "uk": ("суми", "дати", "час", "номери"),
        "el": ("ποσά", "ημερομηνίες", "ώρες", "αναφορές"),
    }[language]
    for label, values in zip(labels, (amounts, dates, times, identifiers)):
        if values:
            parts.append(f"{label}: {', '.join(values[:4])}")
    if not parts:
        return ""
    prefix = "• " if language != "ar" else "• "
    return prefix + "؛ ".join(parts) + "."


def build_journey_plain_explanation(
    draft: str,
    *,
    conversation_language: str,
) -> str | None:
    summary = build_journey_companion_summary(
        draft,
        conversation_language=conversation_language,
    )
    if summary is None:
        return None
    language = _selected_language(conversation_language)
    journey = classify_journey_draft(draft)
    heading = {
        "ar": "شرح مبسّط للمحتوى:",
        "de": "Einfache Erklärung des Inhalts:",
        "en": "Plain-language explanation:",
        "uk": "Просте пояснення змісту:",
        "el": "Απλή εξήγηση του περιεχομένου:",
    }[language]
    facts = _facts_line(draft, language)
    boundaries = {
        JOURNEY_REFUND: {
            "ar": "• الرسالة تطلب المراجعة أو الاسترداد فقط؛ لا تضمن النتيجة ولا تثبت أن المال دُفع.",
            "de": "• Das Schreiben bittet nur um Prüfung oder Erstattung; es garantiert kein Ergebnis und belegt keine Zahlung.",
            "en": "• The letter only requests review or reimbursement; it does not guarantee an outcome or prove payment.",
            "uk": "• Лист лише просить перевірку або відшкодування; він не гарантує результат і не підтверджує виплату.",
            "el": "• Η επιστολή ζητά μόνο έλεγχο ή επιστροφή· δεν εγγυάται αποτέλεσμα ούτε αποδεικνύει πληρωμή.",
        },
        JOURNEY_APPOINTMENT: {
            "ar": "• الرسالة تطلب إجراءً بشأن الموعد؛ لا تعني أن الحجز أو التغيير أو الإلغاء تم بالفعل.",
            "de": "• Das Schreiben bittet um eine Terminhandlung; es bedeutet nicht, dass Buchung, Änderung oder Absage bereits erfolgt ist.",
            "en": "• The letter requests an appointment action; it does not mean booking, change, or cancellation already occurred.",
            "uk": "• Лист просить дію щодо зустрічі; він не означає, що бронювання, зміна чи скасування вже відбулися.",
            "el": "• Η επιστολή ζητά ενέργεια για το ραντεβού· δεν σημαίνει ότι κράτηση, αλλαγή ή ακύρωση έχει ήδη γίνει.",
        },
        JOURNEY_CONTRACT: {
            "ar": "• الرسالة تطلب توضيحًا أو تأكيدًا كتابيًا؛ لا تحكم بأن العقد أو البند صحيح أو باطل قانونيًا.",
            "de": "• Das Schreiben bittet um Klärung oder Bestätigung; es erklärt Vertrag oder Klausel nicht abschließend für wirksam oder unwirksam.",
            "en": "• The letter requests clarification or confirmation; it does not finally declare the contract or clause valid or invalid.",
            "uk": "• Лист просить уточнення або підтвердження; він не оголошує договір чи пункт остаточно чинним або недійсним.",
            "el": "• Η επιστολή ζητά διευκρίνιση ή επιβεβαίωση· δεν κρίνει οριστικά τη σύμβαση ή τη ρήτρα έγκυρη ή άκυρη.",
        },
    }[journey][language]
    not_sent = {
        "ar": "• هذه مسودة للمراجعة ولم تُرسل. أكمل الحقول بين [ ] وراجع البيانات قبل الإرسال.",
        "de": "• Dies ist ein Entwurf und wurde nicht versendet. Fülle Felder in [ ] aus und prüfe die Angaben.",
        "en": "• This is a draft and was not sent. Complete fields in [ ] and review the details before sending.",
        "uk": "• Це чернетка, її не надіслано. Заповни поля в [ ] і перевір дані перед надсиланням.",
        "el": "• Είναι προσχέδιο και δεν έχει σταλεί. Συμπλήρωσε τα πεδία σε [ ] και έλεγξε τα στοιχεία.",
    }[language]
    lines = [heading, f"• {summary}", boundaries]
    if facts:
        lines.append(facts)
    lines.append(not_sent)
    return "\n".join(lines)
