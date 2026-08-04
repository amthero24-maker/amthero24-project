"""Fast deterministic replies for common WhatsApp turns.

This module keeps greeting UX out of the large language model path. Replies are
short, localized, and cycle through bounded variants using the previous topic.
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
    "ar": (
        "أهلًا 👋 أنا سام من AmtHero24.\nعندك رسالة أو ورقة من ألمانيا؟ ابعتها إليّ وبشرحلك شو معناها وشو الخطوة الجاية.",
        "أهلين، أنا معك.\nابعتلي المستند أو اكتبلي المعاملة اللي بدك تخلّصها، ومنرتّبها سوا خطوة بخطوة.",
        "مرحبًا 👋\nإذا بدك نفهم خطاب، نكتب إيميل ألماني، نعمل اعتراض أو إلغاء، ابدأ بإرسال الورقة أو وصف الموضوع بسطر واحد.",
    ),
    "de": (
        "Hallo 👋 Ich bin Sam von AmtHero24.\nHast du einen Brief oder ein Dokument aus Deutschland? Schick es mir, dann erkläre ich dir, was es bedeutet und was als Nächstes zu tun ist.",
        "Hallo, ich bin da.\nSchick mir das Dokument oder beschreibe kurz, was du erledigen möchtest. Wir ordnen es Schritt für Schritt.",
        "Hi 👋\nOb Brief, deutsche E-Mail, Widerspruch oder Kündigung: Schick mir den ersten Hinweis, dann legen wir direkt los.",
    ),
    "en": (
        "Hi 👋 I’m Sam from AmtHero24.\nGot a German letter or document? Send it and I’ll explain what it means and the next practical step.",
        "Hi, I’m here.\nSend the document or describe the task in one sentence, and we’ll sort it out step by step.",
        "Hello 👋\nFor a German email, objection, cancellation, appointment, or document, send the first detail and we’ll start directly.",
    ),
    "uk": (
        "Привіт 👋 Я Сем з AmtHero24.\nЄ німецький лист або документ? Надішли його — поясню зміст і наступний практичний крок.",
        "Привіт, я на зв’язку.\nНадішли документ або коротко опиши справу, і ми впорядкуємо все крок за кроком.",
        "Вітаю 👋\nДля листа, заперечення, розірвання, запису чи документа надішли першу деталь — почнемо одразу.",
    ),
    "el": (
        "Γεια 👋 Είμαι ο Sam από το AmtHero24.\nΈχεις γερμανική επιστολή ή έγγραφο; Στείλ’ το και θα εξηγήσω τι σημαίνει και ποιο είναι το επόμενο βήμα.",
        "Γεια, είμαι εδώ.\nΣτείλε το έγγραφο ή περιέγραψε σύντομα την υπόθεση και θα την οργανώσουμε βήμα βήμα.",
        "Καλώς ήρθες 👋\nΓια email, ένσταση, ακύρωση, ραντεβού ή έγγραφο, στείλε την πρώτη πληροφορία και ξεκινάμε αμέσως.",
    ),
}


def fast_greeting_answer(text: str, language: str, previous_topic: str = "") -> tuple[str, str] | None:
    """Return a short greeting reply without calling the model."""
    if _normalize(text) not in {_normalize(item) for item in _GREETINGS}:
        return None
    lang = language if language in SUPPORTED_LANGUAGES else "de"
    cycle = {"greeting_1": 1, "greeting_2": 2}
    index = cycle.get(previous_topic, 0)
    return _GREETING_REPLIES[lang][index], f"greeting_{index + 1}"
