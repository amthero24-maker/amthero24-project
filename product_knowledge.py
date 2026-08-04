"""Authoritative AmtHero24 product facts and localized capability answers."""
from __future__ import annotations

import re
import unicodedata

from sam_fast_reply import fast_greeting_answer

SUPPORTED_LANGUAGES = ("de", "ar", "en", "uk", "el")


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").casefold().strip()
    value = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", value)
    value = "".join(character if character.isalnum() or character.isspace() else " " for character in value)
    return re.sub(r"\s+", " ", value).strip()


_FOUNDER_PATTERNS = (
    "شو اسم مؤسس الشركة", "مين مؤسس الشركة", "من مؤسس الشركة", "مين صاحب الشركة", "من صاحب الشركة",
    "مين صنعك", "من صنعك", "مين طورك", "من طورك",
    "wer ist der gründer", "wer ist der gruender", "wer hat dich entwickelt",
    "who is the founder", "who founded amthero24", "who made you", "who developed you",
    "хто засновник", "хто тебе створив", "ποιος ειναι ο ιδρυτης", "ποιος σε εφτιαξε",
)
_LANGUAGE_PATTERNS = (
    "شو اللغات", "شو لغة", "اي لغات", "أي لغات", "ما هي اللغات", "بتحكي لغات", "شو بتحكي", "لغاتك",
    "welche sprachen", "welche sprache", "sprichst du", "sprachen kannst du",
    "what languages", "which languages", "languages do you speak",
    "якими мовами", "які мови", "мови ти",
    "ποιες γλωσσες", "τι γλωσσες", "γλωσσες μιλας",
)
_CAPABILITY_PATTERNS = (
    "شو بتقدم", "شو بتعمل", "شو بتقدر", "بشو بتساعد", "كيف بتساعد", "شو خدماتك", "شو فيك تعمل",
    "الميزات", "ميزاتك", "شو الميزات", "الخدمات", "اعرض الميزات", "ورجيني الميزات",
    "was kannst du", "wobei hilfst du", "was machst du", "deine funktionen", "funktionen", "leistungen",
    "what can you do", "how can you help", "what do you offer", "features", "show features",
    "що ти можеш", "чим ти допомагаєш", "можливості",
    "τι μπορεις να κανεις", "πως μπορεις να βοηθησεις", "δυνατοτητες",
)
_MORE_PATTERNS = {
    "تاني", "ثاني", "كمان", "شو كمان", "وغير", "غير هيك", "noch", "mehr", "sonst noch",
    "what else", "anything else", "more", "ще", "ще щось", "τι αλλο", "αλλο",
}

_FOUNDER_ANSWERS = {
    "ar": "مؤسس ومالك AmtHero24 هو Wissam Zidan. وأنا سام، تم تطويري داخل AmtHero24 بإشرافه، لكن ردودي ما بيكتبها Wissam بنفسه.",
    "de": "Gründer und Inhaber von AmtHero24 ist Wissam Zidan. Ich bin Sam und wurde innerhalb von AmtHero24 unter seiner Leitung entwickelt; meine Antworten werden nicht persönlich von ihm geschrieben.",
    "en": "AmtHero24 was founded and is owned by Wissam Zidan. I’m Sam, developed within AmtHero24 under his direction; he does not personally write my replies.",
    "uk": "Засновник і власник AmtHero24 — Wissam Zidan. Я Сем, розроблений у AmtHero24 під його керівництвом; він не пише мої відповіді особисто.",
    "el": "Ιδρυτής και ιδιοκτήτης του AmtHero24 είναι ο Wissam Zidan. Είμαι ο Sam και αναπτύχθηκα μέσα στο AmtHero24 υπό την επίβλεψή του· δεν γράφει ο ίδιος τις απαντήσεις μου.",
}
_LANGUAGE_ANSWERS = {
    "ar": "بحكي معك بالعربية، الألمانية، الإنجليزية، الأوكرانية واليونانية. غالبًا بكتشف لغتك من أول رسالة وبكمّل فيها، وبتقدر تغيّرها بأي وقت.",
    "de": "Ich helfe auf Deutsch, Arabisch, Englisch, Ukrainisch und Griechisch. Meist erkenne ich deine Sprache automatisch; du kannst sie jederzeit wechseln.",
    "en": "I can help in German, Arabic, English, Ukrainian, and Greek. I usually detect your language automatically, and you can switch at any time.",
    "uk": "Я допомагаю німецькою, арабською, англійською, українською та грецькою. Зазвичай мова визначається автоматично, але її можна змінити будь-коли.",
    "el": "Μπορώ να βοηθήσω στα γερμανικά, αραβικά, αγγλικά, ουκρανικά και ελληνικά. Συνήθως αναγνωρίζω αυτόματα τη γλώσσα σου και μπορείς να την αλλάξεις οποτεδήποτε.",
}
_CAPABILITY_ANSWERS = {
    "ar": "ابعتلي رسالة، فاتورة، عقد أو صورة مستند، وبشرحلك شو المهم وشو الخطوة الجاية. وبقدر أكتبلك إيميل، اعتراض أو إلغاء رسمي بالألماني، وأرتّب معك الموعد أو المعاملة خطوة بخطوة.",
    "de": "Schick mir einen Brief, eine Rechnung, einen Vertrag oder ein Dokumentfoto. Ich erkläre das Wesentliche und den nächsten Schritt. Außerdem formuliere ich deutsche E-Mails, Widersprüche und Kündigungen und ordne Termine oder Verfahren Schritt für Schritt.",
    "en": "Send me a German letter, invoice, contract, or document image. I’ll explain what matters and the next step. I can also draft formal German emails, objections, and cancellations and guide you through appointments or procedures.",
    "uk": "Надішли німецький лист, рахунок, договір або фото документа. Я поясню головне й наступний крок. Також підготую офіційний лист, заперечення чи розірвання німецькою та допоможу з записом або процедурою.",
    "el": "Στείλε γερμανική επιστολή, λογαριασμό, σύμβαση ή φωτογραφία εγγράφου. Θα εξηγήσω το βασικό και το επόμενο βήμα. Μπορώ επίσης να συντάξω επίσημο email, ένσταση ή ακύρωση στα γερμανικά και να οργανώσω ραντεβού ή διαδικασίες.",
}
_MORE_LANGUAGE_ANSWERS = {
    "ar": "اللغات المدعومة حاليًا هي العربية، الألمانية، الإنجليزية، الأوكرانية واليونانية. اكتبلي بأي لغة بدك نكمّل.",
    "de": "Unterstützt werden Deutsch, Arabisch, Englisch, Ukrainisch und Griechisch. Sag einfach, in welcher Sprache wir weitermachen.",
    "en": "The supported languages are German, Arabic, English, Ukrainian, and Greek. Tell me which one you prefer.",
    "uk": "Підтримуються німецька, арабська, англійська, українська та грецька. Скажи, якою мовою продовжити.",
    "el": "Υποστηρίζονται γερμανικά, αραβικά, αγγλικά, ουκρανικά και ελληνικά. Πες μου σε ποια γλώσσα να συνεχίσουμε.",
}
_MORE_CAPABILITY_ANSWERS = {
    "ar": "كمان فيني أطلع الموعد النهائي، المبلغ والرقم المرجعي إذا كانوا موجودين، وأجهّزلك رد مناسب بدل ما تضل محتار شو تعمل.",
    "de": "Ich kann außerdem Fristen, Beträge und Aktenzeichen erkennen, sofern sie im Dokument stehen, und eine passende Antwort vorbereiten.",
    "en": "I can also identify deadlines, amounts, and reference numbers when present and prepare a suitable response.",
    "uk": "Також можу знайти строк, суму й номер справи, якщо вони є в документі, та підготувати відповідь.",
    "el": "Μπορώ επίσης να εντοπίσω προθεσμίες, ποσά και αριθμούς αναφοράς όταν υπάρχουν και να ετοιμάσω κατάλληλη απάντηση.",
}


def _contains_any(value: str, patterns: tuple[str, ...]) -> bool:
    return any(_normalize(pattern) in value for pattern in patterns)


def product_answer(text: str, language: str, previous_topic: str = "") -> tuple[str, str] | None:
    """Return an authoritative localized answer and a safe business topic."""
    greeting = fast_greeting_answer(text, language, previous_topic)
    if greeting is not None:
        return greeting

    normalized = _normalize(text)
    lang = language if language in SUPPORTED_LANGUAGES else "de"
    if _contains_any(normalized, _FOUNDER_PATTERNS):
        return _FOUNDER_ANSWERS[lang], "identity"
    if _contains_any(normalized, _LANGUAGE_PATTERNS):
        return _LANGUAGE_ANSWERS[lang], "languages"
    if _contains_any(normalized, _CAPABILITY_PATTERNS):
        return _CAPABILITY_ANSWERS[lang], "capabilities"
    if normalized in {_normalize(item) for item in _MORE_PATTERNS}:
        if previous_topic == "languages":
            return _MORE_LANGUAGE_ANSWERS[lang], "languages"
        if previous_topic == "capabilities":
            return _MORE_CAPABILITY_ANSWERS[lang], "capabilities"
    return None
