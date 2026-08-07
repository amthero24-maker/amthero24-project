"""Deterministic helpers for language and conversational context."""
from __future__ import annotations

import re
import unicodedata

LANGUAGE_NAMES = {"de": "German", "ar": "Arabic", "en": "English", "uk": "Ukrainian", "el": "Greek"}

LANGUAGE_COMMANDS = {
    "ar": ("بالعربي", "بالعربية", "عربي"),
    "de": ("بالألماني", "بالالماني", "auf deutsch", "deutsch"),
    "en": ("بالانجليزي", "بالإنجليزي", "english", "in english"),
    "uk": ("بالأوكراني", "українською"),
    "el": ("باليوناني", "στα ελληνικά"),
}

TRANSIENT_CONVERSATION_TOPICS = frozenset({
    "identity", "languages", "capabilities", "greeting",
})

_BRIEF_COMMANDS = {
    "اختصر", "اختصرها", "اختصرلي", "باختصار", "مختصر",
    "kurz", "kürzer", "kurz zusammenfassen", "fass es kurz zusammen",
    "briefly", "shorter", "summarize briefly", "make it shorter",
    "коротко", "скороти", "коротше",
    "σύντομα", "πιο σύντομα", "συντόμευσέ το",
}
_DETAIL_COMMANDS = {
    "اشرح اكثر", "اشرح أكثر", "اشرحلي اكثر", "اشرحلي أكثر", "وضح اكثر", "وضّح أكثر", "بالتفصيل",
    "mehr erklären", "erkläre mehr", "ausführlicher", "genauer erklären",
    "explain more", "more detail", "explain in more detail", "elaborate",
    "поясни більше", "детальніше", "поясни детальніше",
    "εξήγησε περισσότερο", "περισσότερες λεπτομέρειες", "πιο αναλυτικά",
}

_ENGLISH_WORDS = {
    "a", "after", "an", "are", "can", "could", "do", "does", "for", "founder", "hello", "help", "how",
    "i", "is", "later", "me", "minute", "minutes", "my", "of", "openai", "please", "remind", "reminder",
    "sleep", "sleeping", "the", "this", "what", "where", "who", "why", "you", "your", "chatgpt",
    "explain", "shorter", "more", "thanks",
}
_GERMAN_WORDS = {
    "aber", "auf", "bist", "bitte", "das", "dein", "der", "deutsch", "die", "du",
    "erkläre", "gruender", "gründer", "hallo", "hilfe", "ich", "ist", "kannst", "kurz",
    "mehr", "mein", "mit", "nicht", "und", "was", "wer", "wie", "wo", "warum", "danke",
}
_ENGLISH_PHRASES = (
    "who are you", "what can you do", "are you chatgpt", "are you openai",
    "who is the founder", "ignore previous instructions", "show your system prompt",
)
_GERMAN_PHRASES = (
    "wer bist du", "was kannst du", "bist du chatgpt", "bist du openai",
    "wer ist der gründer", "ignoriere vorherige anweisungen", "zeige deinen system prompt",
)


def _normalize_command(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").casefold().strip()
    value = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", value)
    value = re.sub(r"[؟،؛!?.,:;]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def explicit_language_request(text: str) -> str | None:
    lowered = _normalize_command(text)
    for code, commands in LANGUAGE_COMMANDS.items():
        if lowered in {_normalize_command(item) for item in commands}:
            return code
    return None


def response_detail_request(text: str) -> str | None:
    """Return an explicit request to shorten or expand the previous answer."""
    normalized = _normalize_command(text)
    if normalized in {_normalize_command(item) for item in _BRIEF_COMMANDS}:
        return "brief"
    if normalized in {_normalize_command(item) for item in _DETAIL_COMMANDS}:
        return "detailed"
    return None


def is_transient_conversation_topic(topic: str) -> bool:
    """Return true for meta-conversation topics that must not replace mission context."""
    normalized = str(topic or "").strip().casefold()
    return normalized in TRANSIENT_CONVERSATION_TOPICS or normalized.startswith("greeting_")


def persistent_mission_topic(candidate: str, previous: str = "") -> str:
    """Keep the last real mission topic when a turn is only about Sam or the product."""
    current = str(candidate or "").strip()
    prior = str(previous or "").strip()
    if not current or is_transient_conversation_topic(current):
        return prior
    return current


def _latin_language_score(value: str) -> tuple[int, int]:
    normalized = _normalize_command(value)
    words = set(re.findall(r"[a-zäöüß]+", normalized))
    english = sum(word in _ENGLISH_WORDS for word in words)
    german = sum(word in _GERMAN_WORDS for word in words)
    english += 3 * sum(phrase in normalized for phrase in _ENGLISH_PHRASES)
    german += 3 * sum(phrase in normalized for phrase in _GERMAN_PHRASES)
    if re.search(r"[äöüß]", normalized):
        german += 2
    return english, german


def detect_language(text: str, fallback: str = "de") -> str:
    requested = explicit_language_request(text)
    if requested:
        return requested
    value = text or ""
    if re.search(r"[\u0600-\u06FF]", value):
        return "ar"
    if re.search(r"[\u0400-\u04FF]", value):
        return "uk"
    if re.search(r"[\u0370-\u03FF]", value):
        return "el"
    if re.search(r"[A-Za-zÄÖÜäöüß]", value):
        english, german = _latin_language_score(value)
        if english > german:
            return "en"
        if german > english:
            return "de"
        return fallback if fallback in {"de", "en"} else "de"
    return fallback if fallback in LANGUAGE_NAMES else "de"


def extract_city(text: str) -> str:
    patterns = (
        r"(?:ساكن|عايش|مقيم)\s+(?:في|ب)\s*([\u0600-\u06FF -]{2,35})",
        r"(?:ich wohne|ich lebe)\s+(?:in\s+)?([A-Za-zÄÖÜäöüß -]{2,35})",
        r"i live\s+in\s+([A-Za-z -]{2,35})",
    )
    for pattern in patterns:
        match = re.search(pattern, text or "", re.IGNORECASE)
        if match:
            return re.split(r"[,.!?؟]", match.group(1))[0].strip()[:35]
    return ""


def infer_topic(text: str, previous: str = "") -> str:
    lowered = (text or "").casefold()
    rules = {
        "housing": ("wohnung", "miete", "سكن", "إيجار", "اجار"),
        "work": ("arbeit", "job", "gehalt", "شغل", "عمل", "راتب"),
        "invoice": ("rechnung", "rate", "mahnung", "فاتورة", "قسط"),
        "residence": ("aufenthalt", "visum", "اقامة", "إقامة", "فيزا"),
        "benefits": ("jobcenter", "bürgergeld", "kindergeld", "مساعدات"),
        "health": ("krankenkasse", "arzt", "versicherung", "تأمين", "طبيب"),
        "document": ("brief", "email", "formular", "bescheid", "رسالة", "ايميل", "وثيقة"),
    }
    for topic, terms in rules.items():
        if any(term.casefold() in lowered for term in terms):
            return topic
    return previous


def build_effective_user_text(text: str, profile: dict[str, object]) -> str:
    requested = explicit_language_request(text)
    detail = response_detail_request(text)
    previous = str(
        profile.get("session_last_reply")
        or profile.get("last_assistant_reply")
        or ""
    ).strip()
    if requested:
        language_name = LANGUAGE_NAMES[requested]
        if previous:
            return f"Restate the previous answer in {language_name} only, preserving its meaning. Previous answer: {previous}"
        return f"Continue in {language_name} only and ask what help is needed."
    if detail == "brief":
        if previous:
            return f"Rewrite the previous answer more briefly without losing the essential fact or next step. Previous answer: {previous}"
        return "Answer briefly in complete sentences and keep one concrete next step."
    if detail == "detailed":
        if previous:
            return f"Explain the previous answer in more detail without inventing facts, and keep the structure practical. Previous answer: {previous}"
        return "Explain the current topic in more detail without inventing facts, then give one concrete next step."
    return text
