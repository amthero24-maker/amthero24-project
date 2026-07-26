"""Deterministic, privacy-conscious action intelligence for German documents."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

_SUPPORTED_LANGUAGES = {"de", "ar", "en", "uk", "el"}
_DATE_PATTERN = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-]((?:20)?\d{2})\b")
_AMOUNT_PATTERN = re.compile(
    r"(?<!\w)(?:€\s*)?\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})?\s*(?:€|EUR)(?!\w)",
    re.IGNORECASE,
)
_REFERENCE_PATTERN = re.compile(
    r"\b(?:aktenzeichen|geschäftszeichen|unser zeichen|kundennummer|rechnungs(?:nummer|nr\.?|\s*nr\.?)|"
    r"bg[-\s]?nummer|vorgangsnummer|referenz(?:nummer)?|zeichen)\s*[:#]?\s*([A-Z0-9][A-Z0-9/._ -]{2,40})",
    re.IGNORECASE,
)
_DEADLINE_CONTEXT = re.compile(
    r"(?:frist|bis\s+zum|spätestens|zahlbar\s+bis|fällig\s+am|termin\s+am|"
    r"widerspruch[^\n.]{0,80}?bis|antwort[^\n.]{0,50}?bis|einzureichen\s+bis|vorzusprechen\s+am)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DocumentAnalysis:
    category: str
    authority: str
    deadline: str | None
    amounts: tuple[str, ...]
    references: tuple[str, ...]
    title: str
    next_step: str
    urgency: str
    actionable: bool
    source_kind: str

    def pending_action(self) -> dict[str, Any] | None:
        if not self.actionable:
            return None
        return {
            "title": self.title,
            "topic": self.category,
            "due_at": self.deadline,
            "next_step": self.next_step,
            "authority": self.authority,
            "source_kind": self.source_kind,
        }


def _clean(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = "".join(character for character in text if unicodedata.category(character) not in {"Cf", "Cs"})
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(match: re.Match[str]) -> date | None:
    day, month, year = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _deadline(text: str) -> date | None:
    candidates: list[tuple[int, date]] = []
    for match in _DATE_PATTERN.finditer(text):
        parsed = _parse_date(match)
        if not parsed:
            continue
        left = max(0, match.start() - 110)
        right = min(len(text), match.end() + 40)
        context = text[left:right]
        if _DEADLINE_CONTEXT.search(context):
            candidates.append((match.start(), parsed))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[1], item[0]))
    return candidates[0][1]


def _unique(matches: list[str], limit: int = 5) -> tuple[str, ...]:
    output: list[str] = []
    seen: set[str] = set()
    for value in matches:
        cleaned = _clean(value).strip(" .,:;-")
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            output.append(cleaned[:80])
        if len(output) >= limit:
            break
    return tuple(output)


def _category(text: str) -> tuple[str, str]:
    lowered = text.casefold()
    rules = (
        ("jobcenter", "Jobcenter", ("jobcenter", "bürgergeld", "bedarfsgemeinschaft", "bewilligungsbescheid")),
        ("residence", "Ausländerbehörde", ("ausländerbehörde", "aufenthaltstitel", "aufenthaltserlaubnis", "niederlassungserlaubnis")),
        ("health_insurance", "Krankenkasse", ("krankenkasse", "krankenversicherung", "aok", "tk", "barmer", "ikk", "wkk")),
        ("tax", "Finanzamt", ("finanzamt", "steuerbescheid", "einkommensteuer", "steuernummer")),
        ("housing", "Vermieter/Hausverwaltung", ("miete", "vermieter", "hausverwaltung", "nebenkosten", "mietvertrag")),
        ("family_benefits", "Familienkasse", ("familienkasse", "kindergeld", "kinderzuschlag")),
        ("employment", "Arbeitgeber/Agentur für Arbeit", ("arbeitsvertrag", "kündigung", "arbeitgeber", "agentur für arbeit", "arbeitslosengeld")),
        ("invoice", "Rechnungsteller", ("rechnung", "mahnung", "zahlungsaufforderung", "inkasso", "offener betrag")),
        ("appointment", "Behörde", ("termin", "vorsprache", "einladung", "erscheinen sie")),
    )
    for category, authority, keywords in rules:
        if any(keyword in lowered for keyword in keywords):
            return category, authority
    return "document", ""


def _labels(language: str, category: str, authority: str, deadline: str | None) -> tuple[str, str]:
    lang = language if language in _SUPPORTED_LANGUAGES else "de"
    category_names = {
        "invoice": {"ar": "فاتورة أو مطالبة دفع", "de": "Rechnung oder Zahlungsforderung", "en": "invoice or payment request", "uk": "рахунок або вимога оплати", "el": "τιμολόγιο ή απαίτηση πληρωμής"},
        "jobcenter": {"ar": "ملف Jobcenter", "de": "Jobcenter-Schreiben", "en": "Jobcenter letter", "uk": "лист Jobcenter", "el": "έγγραφο Jobcenter"},
        "residence": {"ar": "ملف الإقامة", "de": "Aufenthaltsangelegenheit", "en": "residence matter", "uk": "питання проживання", "el": "θέμα άδειας διαμονής"},
        "health_insurance": {"ar": "ملف التأمين الصحي", "de": "Krankenkassenangelegenheit", "en": "health-insurance matter", "uk": "питання медичного страхування", "el": "θέμα ασφάλισης υγείας"},
        "tax": {"ar": "ملف الضرائب", "de": "Steuerangelegenheit", "en": "tax matter", "uk": "податкове питання", "el": "φορολογικό θέμα"},
        "housing": {"ar": "موضوع السكن", "de": "Wohnungsangelegenheit", "en": "housing matter", "uk": "житлове питання", "el": "στεγαστικό θέμα"},
        "family_benefits": {"ar": "ملف Familienkasse", "de": "Familienkassenangelegenheit", "en": "family-benefits matter", "uk": "питання сімейних виплат", "el": "θέμα οικογενειακών παροχών"},
        "employment": {"ar": "موضوع العمل", "de": "Arbeitsangelegenheit", "en": "employment matter", "uk": "трудове питання", "el": "εργασιακό θέμα"},
        "appointment": {"ar": "موعد رسمي", "de": "Behördentermin", "en": "official appointment", "uk": "офіційна зустріч", "el": "επίσημο ραντεβού"},
        "document": {"ar": "متابعة المستند", "de": "Dokument prüfen", "en": "document follow-up", "uk": "перевірка документа", "el": "έλεγχος εγγράφου"},
    }
    name = category_names.get(category, category_names["document"])[lang]
    if lang == "ar":
        title = f"متابعة {name}"
        next_step = "راجع المطلوب واتخذ الإجراء المناسب" + (f" قبل {deadline}" if deadline else "")
    elif lang == "de":
        title = name
        next_step = "Anforderung prüfen und den nächsten Schritt erledigen" + (f" – Frist {deadline}" if deadline else "")
    elif lang == "en":
        title = name.capitalize()
        next_step = "Review the request and complete the next step" + (f" before {deadline}" if deadline else "")
    elif lang == "uk":
        title = name.capitalize()
        next_step = "Перевірити вимогу та виконати наступний крок" + (f" до {deadline}" if deadline else "")
    else:
        title = name.capitalize()
        next_step = "Έλεγξε την απαίτηση και κάνε το επόμενο βήμα" + (f" έως {deadline}" if deadline else "")
    if authority:
        title = f"{title} – {authority}"
    return title[:180], next_step[:300]


def analyze_document_text(
    text: str,
    *,
    language: str = "de",
    source_kind: str = "document",
    today: date | None = None,
) -> DocumentAnalysis:
    cleaned = _clean(text)[:16_000]
    category, authority = _category(cleaned)
    deadline_date = _deadline(cleaned)
    deadline = deadline_date.isoformat() if deadline_date else None
    amounts = _unique([match.group(0) for match in _AMOUNT_PATTERN.finditer(cleaned)])
    references = _unique([match.group(1) for match in _REFERENCE_PATTERN.finditer(cleaned)], limit=4)

    current = today or datetime.now(UTC).date()
    urgency = "normal"
    if deadline_date:
        remaining = (deadline_date - current).days
        urgency = "overdue" if remaining < 0 else ("high" if remaining <= 7 else ("medium" if remaining <= 30 else "normal"))

    action_keywords = (
        "frist", "zahlen", "zahlung", "überweisen", "antworten", "einreichen", "nachreichen",
        "widerspruch", "termin", "vorsprechen", "unterschreiben", "kündigung", "mahnung",
        "aufforderung", "mitwirkung", "antrag", "bescheid",
    )
    lowered = cleaned.casefold()
    actionable = bool(deadline or amounts or any(keyword in lowered for keyword in action_keywords))
    title, next_step = _labels(language, category, authority, deadline)
    return DocumentAnalysis(
        category=category,
        authority=authority,
        deadline=deadline,
        amounts=amounts,
        references=references,
        title=title,
        next_step=next_step,
        urgency=urgency,
        actionable=actionable,
        source_kind=source_kind[:20] or "document",
    )


def prompt_facts(analysis: DocumentAnalysis, *, language: str) -> str:
    lang = language if language in _SUPPORTED_LANGUAGES else "de"
    labels = {
        "ar": ("حقائق مستخرجة آليًا يجب التحقق منها مع النص", "المهلة", "المبالغ", "أرقام المرجع", "الجهة", "الاستعجال"),
        "de": ("Automatisch extrahierte Hinweise, die mit dem Text abzugleichen sind", "Frist", "Beträge", "Referenzen", "Stelle", "Dringlichkeit"),
        "en": ("Automatically extracted hints to verify against the text", "Deadline", "Amounts", "References", "Authority", "Urgency"),
        "uk": ("Автоматично витягнуті підказки, які слід звірити з текстом", "Термін", "Суми", "Посилання", "Установа", "Терміновість"),
        "el": ("Αυτόματα εξαγόμενες ενδείξεις που πρέπει να ελεγχθούν με το κείμενο", "Προθεσμία", "Ποσά", "Αναφορές", "Υπηρεσία", "Επείγον"),
    }[lang]
    lines = [labels[0] + ":"]
    if analysis.deadline:
        lines.append(f"- {labels[1]}: {analysis.deadline}")
    if analysis.amounts:
        lines.append(f"- {labels[2]}: {', '.join(analysis.amounts)}")
    if analysis.references:
        lines.append(f"- {labels[3]}: {', '.join(analysis.references)}")
    if analysis.authority:
        lines.append(f"- {labels[4]}: {analysis.authority}")
    lines.append(f"- {labels[5]}: {analysis.urgency}")
    if analysis.actionable:
        save_instruction = {
            "ar": "اختم بجملة قصيرة: إذا بتحب أسجّلها كمهمة للمتابعة، جاوب «نعم سجّلها». لا تدّعي أنها حُفظت بعد.",
            "de": "Beende mit einem kurzen Satz: Wenn du das als Aufgabe speichern möchtest, antworte „Ja, speichern“. Behaupte nicht, dass es bereits gespeichert wurde.",
            "en": "End with one short sentence: To save this as a follow-up task, reply “Yes, save it”. Do not claim it is already saved.",
            "uk": "Заверши коротко: щоб зберегти це як завдання, напиши «Так, зберегти». Не стверджуй, що воно вже збережене.",
            "el": "Κλείσε σύντομα: για αποθήκευση ως εργασία, απάντησε «Ναι, αποθήκευσέ το». Μην πεις ότι έχει ήδη αποθηκευτεί.",
        }[lang]
        lines.append(save_instruction)
    return "\n".join(lines)
