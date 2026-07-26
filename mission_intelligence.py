"""Natural-language extensions for the deterministic Mission Engine."""
from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Callable

from conversation_intelligence import detect_language
from mission_engine import MissionIntent

_DUE_PREFIX = "@mission-due:"

_TOPIC_TITLES = {
    "ar": {
        "invoice": "متابعة الفاتورة أو الدفعة", "document": "متابعة المستند أو الرسالة",
        "housing": "متابعة موضوع السكن", "work": "متابعة موضوع العمل والراتب",
        "residence": "متابعة الإقامة أو الفيزا", "benefits": "متابعة المساعدات أو الدائرة",
        "health": "متابعة التأمين أو الصحة",
    },
    "de": {
        "invoice": "Rechnung oder Zahlung verfolgen", "document": "Dokument oder Schreiben verfolgen",
        "housing": "Wohnungsthema verfolgen", "work": "Arbeit oder Gehalt verfolgen",
        "residence": "Aufenthalt oder Visum verfolgen", "benefits": "Leistung oder Behördenthema verfolgen",
        "health": "Versicherung oder Gesundheit verfolgen",
    },
    "en": {
        "invoice": "Follow up invoice or payment", "document": "Follow up document or letter",
        "housing": "Follow up housing issue", "work": "Follow up work or salary issue",
        "residence": "Follow up residence or visa", "benefits": "Follow up benefits or authority issue",
        "health": "Follow up insurance or health issue",
    },
    "uk": {
        "invoice": "Відстеження рахунку або платежу", "document": "Відстеження документа або листа",
        "housing": "Відстеження питання житла", "work": "Відстеження роботи або зарплати",
        "residence": "Відстеження проживання або візи", "benefits": "Відстеження виплат або установи",
        "health": "Відстеження страхування або здоров’я",
    },
    "el": {
        "invoice": "Παρακολούθηση τιμολογίου ή πληρωμής", "document": "Παρακολούθηση εγγράφου ή επιστολής",
        "housing": "Παρακολούθηση θέματος στέγασης", "work": "Παρακολούθηση εργασίας ή μισθού",
        "residence": "Παρακολούθηση διαμονής ή βίζας", "benefits": "Παρακολούθηση παροχής ή υπηρεσίας",
        "health": "Παρακολούθηση ασφάλισης ή υγείας",
    },
}

_TRACK_REGEXES = (
    r"^\s*(?:تابعلي|تابع معي|سجللي|سجّللي|ضيفلي مهمة|أضف لي مهمة)\s+(.+)$",
    r"^\s*(?:verfolge|speichere als aufgabe)\s+(.+)$",
    r"^\s*(?:track|save as a task)\s+(.+)$",
    r"^\s*(?:відстежуй|збережи як завдання)\s+(.+)$",
    r"^\s*(?:παρακολουθησε|αποθηκευσε ως εργασια)\s+(.+)$",
)


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").casefold().strip()
    value = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", value)
    value = re.sub(r"[؟،؛!?.,:;]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _relative_due_date(text: str, *, now: datetime | None = None) -> str:
    normalized = _normalize(text)
    due_context = (
        "موعد", "مهلة", "ذكرني", "تذكير", "frist", "termin", "erinnere", "deadline", "remind",
        "термін", "нагадай", "προθεσμια", "θυμισε",
    )
    if not any(token in normalized for token in due_context):
        return ""

    current = (now or datetime.now(UTC)).date()
    tomorrow_terms = ("بكرا", "غدا", "غداً", "morgen", "tomorrow", "завтра", "αυριο")
    if any(_normalize(term) in normalized for term in tomorrow_terms):
        return (current + timedelta(days=1)).isoformat()

    patterns = (
        r"بعد\s+(\d{1,3})\s+(?:يوم|ايام|أيام)",
        r"in\s+(\d{1,3})\s+tagen?",
        r"in\s+(\d{1,3})\s+days?",
        r"через\s+(\d{1,3})\s+д",
        r"σε\s+(\d{1,3})\s+ημερ",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            days = max(1, min(int(match.group(1)), 365))
            return (current + timedelta(days=days)).isoformat()
    return ""


def enhanced_detect_mission_intent(
    text: str,
    original_detector: Callable[[str], MissionIntent | None],
    *,
    now: datetime | None = None,
) -> MissionIntent | None:
    original = original_detector(text)
    if original is not None:
        return original

    due = _relative_due_date(text, now=now)
    if due:
        return MissionIntent("create", _DUE_PREFIX + due)

    for pattern in _TRACK_REGEXES:
        match = re.match(pattern, text or "", flags=re.IGNORECASE)
        if match:
            title = " ".join(match.group(1).split()).strip(" -")[:180]
            if title:
                return MissionIntent("create", title)
    return None


def enhanced_mission_title(
    intent: MissionIntent,
    *,
    current_topic: str = "",
    last_message: str = "",
    original_title: Callable[..., str],
) -> str:
    title = original_title(intent, current_topic=current_topic, last_message=last_message)
    normalized = str(title or "").strip()
    if normalized not in _TOPIC_TITLES["de"] and normalized not in {
        "invoice", "document", "housing", "work", "residence", "benefits", "health"
    }:
        return normalized
    language = detect_language(last_message, "de")
    return _TOPIC_TITLES.get(language, _TOPIC_TITLES["de"]).get(normalized, normalized)
