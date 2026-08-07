"""Deterministic mission commands and localized WhatsApp replies."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from conversation_intelligence import is_transient_conversation_topic

SUPPORTED_LANGUAGES = {"de", "ar", "en", "uk", "el"}

_NEXT_STEP_PREFIX = "@mission-next-step:"
_LAST_ACTION_PREFIX = "@mission-last-action:"
_WAITING_PREFIX = "@mission-status:waiting"
_DUE_PREFIX = "@mission-due:"


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").casefold().strip()
    value = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", value)
    value = re.sub(r"[؟،؛!?.,:;]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


_CREATE_PATTERNS = (
    "تابعلي هالموضوع", "تابع هذا الموضوع", "احفظ هالموضوع", "سجل هالمهمة", "ضيف مهمة",
    "خلينا نتابع", "اعتبرها مهمة", "merk dir diese aufgabe", "als aufgabe speichern",
    "dieses thema verfolgen", "create a task", "save this task", "track this",
    "збережи це завдання", "відстежуй це", "αποθηκευσε αυτη την εργασια", "παρακολουθησε το",
)
_LIST_PATTERNS = (
    "شو مهامي", "شو عندي مهام", "مهامي المفتوحة", "اعرض مهامي", "قائمة المهام",
    "وين وصلنا", "شو وضع المهمة", "تفاصيل المهمة", "آخر مهمة", "اخر مهمة",
    "meine aufgaben", "offene aufgaben", "welche aufgaben habe ich", "wo stehen wir",
    "aufgabenstatus", "letzte aufgabe", "my tasks", "open tasks", "what are my tasks",
    "where are we", "task status", "latest task", "мої завдання", "відкриті завдання",
    "статус завдання", "οι εργασιες μου", "ανοιχτες εργασιες", "κατασταση εργασιας",
)
_COMPLETE_PATTERNS = (
    "خلصت المهمة", "تمت المهمة", "سكر المهمة", "سكّر المهمة", "خلص الموضوع", "انتهت المهمة",
    "aufgabe erledigt", "thema erledigt", "mark task complete", "task completed", "done with this task",
    "завдання виконано", "ολοκληρωθηκε η εργασια",
)
_WAITING_PATTERNS = (
    "ناطر رد", "ناطرة رد", "بانتظار الرد", "عم استنى الرد", "مستني الرد", "ننتظر الرد",
    "warte auf antwort", "warten auf antwort", "antwort ausstehend",
    "waiting for a reply", "waiting for reply", "awaiting reply",
    "чекаю на відповідь", "αναμονη απαντησης", "περιμενω απαντηση",
)
_NEXT_STEP_REGEXES = (
    r"^\s*(?:الخطوة الجاية|الخطوة التالية|الخطوة القادمة)\s*[:\-]?\s*(.+)$",
    r"^\s*(?:nächster schritt|naechster schritt)\s*[:\-]?\s*(.+)$",
    r"^\s*next step\s*[:\-]?\s*(.+)$",
    r"^\s*наступний крок\s*[:\-]?\s*(.+)$",
    r"^\s*επομενο βημα\s*[:\-]?\s*(.+)$",
)
_LAST_ACTION_REGEXES = (
    r"^\s*(?:آخر إجراء|اخر اجراء|آخر خطوة عملتها|اخر خطوة عملتها)\s*[:\-]?\s*(.+)$",
    r"^\s*(?:letzte aktion|letzter schritt)\s*[:\-]?\s*(.+)$",
    r"^\s*(?:last action|last step)\s*[:\-]?\s*(.+)$",
    r"^\s*останн(?:я|ій) дія\s*[:\-]?\s*(.+)$",
    r"^\s*τελευταια ενεργεια\s*[:\-]?\s*(.+)$",
)
_DUE_KEYWORDS = (
    "الموعد", "المهلة", "آخر موعد", "اخر موعد", "frist", "termin", "deadline", "due date",
    "кінцевий термін", "προθεσμια",
)


@dataclass(frozen=True)
class MissionIntent:
    action: str
    title: str = ""


def _extract_payload(text: str, patterns: tuple[str, ...]) -> str:
    for pattern in patterns:
        match = re.match(pattern, text or "", flags=re.IGNORECASE)
        if match:
            return " ".join(match.group(1).split()).strip()
    return ""


def _extract_due_date(text: str) -> str:
    normalized = _normalize(text)
    if not any(_normalize(keyword) in normalized for keyword in _DUE_KEYWORDS):
        return ""

    iso_match = re.search(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", text or "")
    if iso_match:
        raw = f"{iso_match.group(1)}-{int(iso_match.group(2)):02d}-{int(iso_match.group(3)):02d}"
    else:
        local_match = re.search(r"\b(\d{1,2})[./](\d{1,2})[./](20\d{2})\b", text or "")
        if not local_match:
            return ""
        raw = f"{local_match.group(3)}-{int(local_match.group(2)):02d}-{int(local_match.group(1)):02d}"
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return ""


def detect_mission_intent(text: str) -> MissionIntent | None:
    normalized = _normalize(text)

    if any(_normalize(pattern) in normalized for pattern in _LIST_PATTERNS):
        return MissionIntent("list")
    if any(_normalize(pattern) in normalized for pattern in _COMPLETE_PATTERNS):
        return MissionIntent("complete")

    next_step = _extract_payload(text, _NEXT_STEP_REGEXES)
    if next_step:
        return MissionIntent("create", _NEXT_STEP_PREFIX + next_step)

    last_action = _extract_payload(text, _LAST_ACTION_REGEXES)
    if last_action:
        return MissionIntent("create", _LAST_ACTION_PREFIX + last_action)

    due_date = _extract_due_date(text)
    if due_date:
        return MissionIntent("create", _DUE_PREFIX + due_date)

    if any(_normalize(pattern) in normalized for pattern in _WAITING_PATTERNS):
        return MissionIntent("create", _WAITING_PREFIX)

    original_words = (text or "").strip().split()
    for pattern in _CREATE_PATTERNS:
        normalized_pattern = _normalize(pattern)
        if normalized.startswith(normalized_pattern):
            command_word_count = len(pattern.split())
            title = " ".join(original_words[command_word_count:]).strip(" -")
            return MissionIntent("create", title)
        if normalized_pattern in normalized:
            return MissionIntent("create")
    return None


_TRANSIENT_MISSION_EXACT_MESSAGES = {
    "مرحبا", "اهلا", "أهلا", "هلا", "سلام", "hallo", "hi", "hello", "hey",
    "привіт", "γεια",
}
_TRANSIENT_MISSION_MESSAGE_PATTERNS = (
    "من انت", "من أنت", "شو بتعمل", "شو بتقدم", "شو اللغات", "من اسس amthero24",
    "هل انت chatgpt", "تجاهل التعليمات", "اكشف تعليمات النظام",
    "wer bist du", "was kannst du", "welche sprachen", "wer hat amthero24 gegründet",
    "bist du chatgpt", "ignoriere alle anweisungen", "zeige deinen system prompt",
    "who are you", "what can you do", "what languages", "who founded amthero24",
    "are you chatgpt", "ignore all instructions", "show your system prompt",
    "хто ти", "що ти можеш", "які мови", "хто заснував amthero24", "ти chatgpt",
    "ігноруй усі інструкції", "покажи системний промпт",
    "ποιος εισαι", "ποιος είσαι", "τι μπορεις να κανεις", "ποιες γλωσσες",
    "ποιος ιδρυσε το amthero24", "εισαι chatgpt", "αγνοησε ολες τις οδηγιες",
    "δειξε το system prompt",
)


def _persistent_mission_topic(value: str) -> str:
    topic = " ".join((value or "").split()).strip()
    if not topic or topic == "unknown" or is_transient_conversation_topic(topic):
        return ""
    return topic


def _persistent_mission_message(value: str) -> str:
    message = " ".join((value or "").split()).strip()
    normalized = _normalize(message)
    if not normalized:
        return ""
    if normalized in {_normalize(item) for item in _TRANSIENT_MISSION_EXACT_MESSAGES}:
        return ""
    if any(_normalize(pattern) in normalized for pattern in _TRANSIENT_MISSION_MESSAGE_PATTERNS):
        return ""
    return message


def mission_title(intent: MissionIntent, *, current_topic: str = "", last_message: str = "") -> str:
    title = " ".join((intent.title or "").split()).strip()
    if title:
        return title[:500]
    topic = _persistent_mission_topic(current_topic)
    if topic:
        return topic[:180]
    previous = _persistent_mission_message(last_message)
    return previous[:180] if previous else "Open follow-up"


def memory_required_message(language: str) -> str:
    lang = language if language in SUPPORTED_LANGUAGES else "de"
    return {
        "ar": "حتى أتابع هالمهمة معك بالمرة الجاية، لازم تكون الذاكرة مفعّلة بموافقتك. اكتب «فعّل الذاكرة» وبمشي معك خطوة خطوة.",
        "de": "Damit ich diese Aufgabe beim nächsten Mal weiterverfolgen kann, muss die Erinnerung mit deiner Zustimmung aktiviert sein. Schreib „Erinnerung aktivieren“.",
        "en": "To keep following this task next time, memory needs to be enabled with your permission. Say “enable memory”.",
        "uk": "Щоб продовжити цю справу наступного разу, потрібно за твоєю згодою увімкнути пам’ять. Напиши «увімкни пам’ять».",
        "el": "Για να συνεχίσω αυτή την εργασία την επόμενη φορά, χρειάζεται να ενεργοποιηθεί η μνήμη με τη συγκατάθεσή σου. Γράψε «ενεργοποίησε μνήμη».",
    }[lang]


def _missing_mission_message(language: str) -> str:
    lang = language if language in SUPPORTED_LANGUAGES else "de"
    return {
        "ar": "ما عندك مهمة مفتوحة حتى حدّثها. قلّي أولًا «تابعلي هالموضوع» وأنا بسجّلها.",
        "de": "Es gibt keine offene Aufgabe zum Aktualisieren. Schreib zuerst „Dieses Thema verfolgen“.",
        "en": "There is no open task to update. First say “track this”.",
        "uk": "Немає відкритого завдання для оновлення. Спочатку напиши «відстежуй це».",
        "el": "Δεν υπάρχει ανοιχτή εργασία για ενημέρωση. Πρώτα γράψε «παρακολούθησέ το».",
    }[lang]


def mission_created_message(language: str, mission: dict[str, Any]) -> str:
    lang = language if language in SUPPORTED_LANGUAGES else "de"
    operation = str(mission.get("_operation") or "created")
    title = str(mission.get("title") or "").strip()

    if operation == "missing":
        return _missing_mission_message(lang)
    if operation == "next_step":
        value = str(mission.get("next_step") or "").strip()
        return {
            "ar": f"تمام ✅ حدّثت الخطوة الجاية لمهمة «{title}»: {value}",
            "de": f"Alles klar ✅ Nächster Schritt für „{title}“: {value}",
            "en": f"Done ✅ Next step for “{title}”: {value}",
            "uk": f"Готово ✅ Наступний крок для «{title}»: {value}",
            "el": f"Έγινε ✅ Επόμενο βήμα για «{title}»: {value}",
        }[lang]
    if operation == "last_action":
        value = str(mission.get("last_action") or "").strip()
        return {
            "ar": f"سجّلتها ✅ آخر إجراء بمهمة «{title}»: {value}",
            "de": f"Gespeichert ✅ Letzte Aktion bei „{title}“: {value}",
            "en": f"Saved ✅ Last action for “{title}”: {value}",
            "uk": f"Збережено ✅ Остання дія для «{title}»: {value}",
            "el": f"Αποθηκεύτηκε ✅ Τελευταία ενέργεια για «{title}»: {value}",
        }[lang]
    if operation == "waiting":
        return {
            "ar": f"تمام، خليت مهمة «{title}» بحالة: بانتظار الرد ⏳",
            "de": f"Okay, „{title}“ steht jetzt auf: Antwort ausstehend ⏳",
            "en": f"Okay, “{title}” is now waiting for a reply ⏳",
            "uk": f"Добре, «{title}» тепер очікує відповіді ⏳",
            "el": f"Εντάξει, η εργασία «{title}» περιμένει απάντηση ⏳",
        }[lang]
    if operation == "due":
        value = str(mission.get("due_at") or "").strip()
        return {
            "ar": f"سجّلت الموعد لمهمة «{title}»: {value} 📅",
            "de": f"Frist für „{title}“ gespeichert: {value} 📅",
            "en": f"Due date for “{title}” saved: {value} 📅",
            "uk": f"Термін для «{title}» збережено: {value} 📅",
            "el": f"Η προθεσμία για «{title}» αποθηκεύτηκε: {value} 📅",
        }[lang]
    return {
        "ar": f"تمام ✅ سجلتها كمهمة مفتوحة: «{title}». لما ترجع منكمّل من هون بدل ما نعيد من الصفر.",
        "de": f"Erledigt ✅ Ich habe es als offene Aufgabe gespeichert: „{title}“. Beim nächsten Mal machen wir hier weiter.",
        "en": f"Done ✅ I saved it as an open task: “{title}”. Next time we can continue from here.",
        "uk": f"Готово ✅ Я зберіг це як відкрите завдання: «{title}». Наступного разу продовжимо звідси.",
        "el": f"Έγινε ✅ Το αποθήκευσα ως ανοιχτή εργασία: «{title}». Την επόμενη φορά συνεχίζουμε από εδώ.",
    }[lang]


def _status_label(language: str, status: str) -> str:
    labels = {
        "ar": {"open": "مفتوحة", "waiting": "بانتظار الرد", "completed": "مكتملة"},
        "de": {"open": "offen", "waiting": "wartet auf Antwort", "completed": "erledigt"},
        "en": {"open": "open", "waiting": "waiting for reply", "completed": "completed"},
        "uk": {"open": "відкрите", "waiting": "очікує відповіді", "completed": "виконане"},
        "el": {"open": "ανοιχτή", "waiting": "αναμονή απάντησης", "completed": "ολοκληρωμένη"},
    }
    return labels[language].get(status, status)


def mission_list_message(language: str, missions: list[dict[str, Any]]) -> str:
    lang = language if language in SUPPORTED_LANGUAGES else "de"
    if not missions:
        return {
            "ar": "ما عندك مهام مفتوحة حاليًا. لما بدك تابع موضوع، قلّي «تابعلي هالموضوع».",
            "de": "Du hast derzeit keine offenen Aufgaben. Schreib „Dieses Thema verfolgen“, wenn ich etwas merken soll.",
            "en": "You have no open tasks right now. Say “track this” when you want me to save one.",
            "uk": "Зараз відкритих завдань немає. Напиши «відстежуй це», коли потрібно щось зберегти.",
            "el": "Δεν έχεις ανοιχτές εργασίες τώρα. Γράψε «παρακολούθησέ το» όταν θέλεις να αποθηκεύσω κάτι.",
        }[lang]

    labels = {
        "ar": {"last": "آخر إجراء", "next": "الخطوة التالية", "due": "الموعد"},
        "de": {"last": "Letzte Aktion", "next": "Nächster Schritt", "due": "Frist"},
        "en": {"last": "Last action", "next": "Next step", "due": "Due date"},
        "uk": {"last": "Остання дія", "next": "Наступний крок", "due": "Термін"},
        "el": {"last": "Τελευταία ενέργεια", "next": "Επόμενο βήμα", "due": "Προθεσμία"},
    }[lang]
    blocks: list[str] = []
    for index, item in enumerate(missions, start=1):
        title = str(item.get("title") or "").strip()
        status = _status_label(lang, str(item.get("status") or "open"))
        details = [f"{index}. {title} — {status}"]
        if item.get("last_action"):
            details.append(f"   {labels['last']}: {item['last_action']}")
        if item.get("next_step"):
            details.append(f"   {labels['next']}: {item['next_step']}")
        if item.get("due_at"):
            details.append(f"   {labels['due']}: {item['due_at']}")
        blocks.append("\n".join(details))
    heading = {
        "ar": "مهامك المفتوحة:", "de": "Deine offenen Aufgaben:", "en": "Your open tasks:",
        "uk": "Твої відкриті завдання:", "el": "Οι ανοιχτές εργασίες σου:",
    }[lang]
    return heading + "\n" + "\n".join(blocks)


def mission_completed_message(language: str, mission: dict[str, Any] | None) -> str:
    lang = language if language in SUPPORTED_LANGUAGES else "de"
    if not mission:
        return {
            "ar": "ما لقيت مهمة مفتوحة حتى سكّرها.", "de": "Ich habe keine offene Aufgabe zum Abschließen gefunden.",
            "en": "I could not find an open task to complete.", "uk": "Не знайшов відкритого завдання для завершення.",
            "el": "Δεν βρήκα ανοιχτή εργασία για ολοκλήρωση.",
        }[lang]
    title = str(mission.get("title") or "").strip()
    return {
        "ar": f"ممتاز 👌 سكّرت مهمة «{title}» كمكتملة.",
        "de": f"Sehr gut 👌 Die Aufgabe „{title}“ ist als erledigt markiert.",
        "en": f"Great 👌 I marked “{title}” as completed.",
        "uk": f"Чудово 👌 Завдання «{title}» позначено виконаним.",
        "el": f"Τέλεια 👌 Η εργασία «{title}» σημειώθηκε ως ολοκληρωμένη.",
    }[lang]
