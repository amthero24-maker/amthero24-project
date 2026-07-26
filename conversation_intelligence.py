"""Deterministic helpers for language and conversational context."""
from __future__ import annotations

import re

LANGUAGE_NAMES = {"de": "German", "ar": "Arabic", "en": "English", "uk": "Ukrainian", "el": "Greek"}

LANGUAGE_COMMANDS = {
    "ar": ("بالعربي", "بالعربية", "عربي"),
    "de": ("بالألماني", "بالالماني", "auf deutsch", "deutsch"),
    "en": ("بالانجليزي", "بالإنجليزي", "english", "in english"),
    "uk": ("بالأوكراني", "українською"),
    "el": ("باليوناني", "στα ελληνικά"),
}


def explicit_language_request(text: str) -> str | None:
    lowered = (text or "").strip().casefold()
    for code, commands in LANGUAGE_COMMANDS.items():
        if lowered in {item.casefold() for item in commands}:
            return code
    return None


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
    lowered = value.casefold()
    if any(word in lowered for word in ("hello", "please", "thanks", "help")):
        return "en"
    if re.search(r"[A-Za-zÄÖÜäöüß]", value):
        return "de"
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
    if not requested:
        return text
    previous = str(profile.get("last_assistant_reply") or "").strip()
    language_name = LANGUAGE_NAMES[requested]
    if previous:
        return f"Restate the previous answer in {language_name} only, preserving its meaning. Previous answer: {previous}"
    return f"Continue in {language_name} only and ask what help is needed."
