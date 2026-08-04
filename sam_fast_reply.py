"""Fast deterministic replies for common WhatsApp turns.

Greeting wording is intentionally kept outside the language-model path and must
never be stored as a business topic or mission title.
"""
from __future__ import annotations

import re
import unicodedata

SUPPORTED_LANGUAGES = ("de", "ar", "en", "uk", "el")


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").casefold().strip()
    value = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", value)
    value = "".join(character if character.isalnum() or character.isspace() else " " for character in value)
    return re.sub(r"\s+", " ", value).strip()


_GREETINGS = {
    "مرحبا", "مرحباً", "اهلا", "أهلا", "هلا", "السلام عليكم", "سلام", "هاي",
    "hallo", "hi", "guten tag", "guten morgen", "guten abend", "hello", "hey",
    "привіт", "добрий день", "γεια", "καλημερα", "καλησπερα",
}

_GREETING_REPLIES = {
    "ar": "أهلًا 👋 أنا سام من AmtHero24.\nعندك رسالة أو ورقة من ألمانيا؟ ابعتها إليّ وبشرحلك شو معناها وشو الخطوة الجاية.",
    "de": "Hallo 👋 Ich bin Sam von AmtHero24.\nHast du einen Brief oder ein Dokument aus Deutschland? Schick es mir, dann erkläre ich dir die Bedeutung und den nächsten Schritt.",
    "en": "Hi 👋 I’m Sam from AmtHero24.\nGot a German letter or document? Send it and I’ll explain what it means and the next practical step.",
    "uk": "Привіт 👋 Я Сем з AmtHero24.\nЄ німецький лист або документ? Надішли його — поясню зміст і наступний практичний крок.",
    "el": "Γεια 👋 Είμαι ο Sam από το AmtHero24.\nΈχεις γερμανική επιστολή ή έγγραφο; Στείλ’ το και θα εξηγήσω τι σημαίνει και ποιο είναι το επόμενο βήμα.",
}


def fast_greeting_answer(text: str, language: str, previous_topic: str = "") -> tuple[str, str] | None:
    """Return a short greeting without creating synthetic conversation state."""
    if _normalize(text) not in {_normalize(item) for item in _GREETINGS}:
        return None
    lang = language if language in SUPPORTED_LANGUAGES else "de"
    safe_topic = "capabilities"
    if previous_topic and not previous_topic.startswith("greeting_"):
        safe_topic = previous_topic
    return _GREETING_REPLIES[lang], safe_topic
