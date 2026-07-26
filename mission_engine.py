"""Deterministic mission commands and localized WhatsApp replies."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

SUPPORTED_LANGUAGES = {"de", "ar", "en", "uk", "el"}


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
    "meine aufgaben", "offene aufgaben", "welche aufgaben habe ich",
    "my tasks", "open tasks", "what are my tasks", "мої завдання", "відкриті завдання",
    "οι εργασιες μου", "ανοιχτες εργασιες",
)
_COMPLETE_PATTERNS = (
    "خلصت المهمة", "تمت المهمة", "سكر المهمة", "سكّر المهمة", "خلص الموضوع", "انتهت المهمة",
    "aufgabe erledigt", "thema erledigt", "mark task complete", "task completed", "done with this task",
    "завдання виконано", "ολοκληρωθηκε η εργασια",
)


@dataclass(frozen=True)
class MissionIntent:
    action: str
    title: str = ""


def detect_mission_intent(text: str) -> MissionIntent | None:
    normalized = _normalize(text)
    if any(_normalize(pattern) in normalized for pattern in _LIST_PATTERNS):
        return MissionIntent("list")
    if any(_normalize(pattern) in normalized for pattern in _COMPLETE_PATTERNS):
        return MissionIntent("complete")
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


def mission_title(intent: MissionIntent, *, current_topic: str = "", last_message: str = "") -> str:
    title = " ".join((intent.title or "").split()).strip()
    if title:
        return title[:180]
    topic = " ".join((current_topic or "").split()).strip()
    if topic and topic not in {"unknown", "capabilities", "languages"}:
        return topic[:180]
    previous = " ".join((last_message or "").split()).strip()
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


def mission_created_message(language: str, mission: dict[str, Any]) -> str:
    lang = language if language in SUPPORTED_LANGUAGES else "de"
    title = str(mission.get("title") or "").strip()
    return {
        "ar": f"تمام ✅ سجلتها كمهمة مفتوحة: «{title}». لما ترجع منكمّل من هون بدل ما نعيد من الصفر.",
        "de": f"Erledigt ✅ Ich habe es als offene Aufgabe gespeichert: „{title}“. Beim nächsten Mal machen wir hier weiter.",
        "en": f"Done ✅ I saved it as an open task: “{title}”. Next time we can continue from here.",
        "uk": f"Готово ✅ Я зберіг це як відкрите завдання: «{title}». Наступного разу продовжимо звідси.",
        "el": f"Έγινε ✅ Το αποθήκευσα ως ανοιχτή εργασία: «{title}». Την επόμενη φορά συνεχίζουμε από εδώ.",
    }[lang]


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
    lines = [f"{index}. {str(item.get('title') or '').strip()}" for index, item in enumerate(missions, start=1)]
    heading = {
        "ar": "مهامك المفتوحة:", "de": "Deine offenen Aufgaben:", "en": "Your open tasks:",
        "uk": "Твої відкриті завдання:", "el": "Οι ανοιχτές εργασίες σου:",
    }[lang]
    return heading + "\n" + "\n".join(lines)


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
