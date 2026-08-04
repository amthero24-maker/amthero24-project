"""Authoritative AmtHero24 product facts and localized capability answers."""
from __future__ import annotations

import re
import unicodedata

SUPPORTED_LANGUAGES = ("de", "ar", "en", "uk", "el")


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").casefold().strip()
    value = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", value)
    value = "".join(character if character.isalnum() or character.isspace() else " " for character in value)
    return re.sub(r"\s+", " ", value).strip()


_GREETING_PATTERNS = {
    "مرحبا", "مرحباً", "اهلا", "أهلا", "هلا", "السلام عليكم", "سلام", "هاي",
    "hallo", "hi", "guten tag", "guten morgen", "guten abend", "hello", "hey",
    "привіт", "добрий день", "γεια", "καλημερα", "καλησπερα",
}

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

_GREETING_ANSWERS = {
    "ar": (
        "أهلًا، أنا سام من AmtHero24. فيني أشرحلك أي رسالة، فاتورة، عقد أو صورة مستند ألماني، "
        "وأطلع لك الموعد النهائي والمبلغ والرقم المرجعي والخطوة المطلوبة إذا كانوا موجودين. "
        "وبقدر كمان أكتب إيميل، اعتراض أو إلغاء رسمي بالألماني، أو أرتّب معك موعد وإجراء خطوة بخطوة. "
        "ابعت المستند أو اكتب شو المعاملة اللي بدك تنجزها."
    ),
    "de": (
        "Hallo, ich bin Sam von AmtHero24. Ich erkläre Briefe, Rechnungen, Verträge und Dokumentfotos, "
        "erkenne Fristen, Beträge, Aktenzeichen und den nächsten erforderlichen Schritt, sofern sie im Dokument stehen. "
        "Außerdem formuliere ich E-Mails, Widersprüche und Kündigungen oder ordne Termine und Verfahren Schritt für Schritt. "
        "Schick das Dokument oder beschreibe kurz, was du erledigen möchtest."
    ),
    "en": (
        "Hello, I’m Sam from AmtHero24. I can explain German letters, invoices, contracts, and document images, "
        "and identify deadlines, amounts, reference numbers, and required next steps when they are present. "
        "I can also draft formal German emails, objections, and cancellations or organize an appointment or procedure step by step. "
        "Send the document or tell me what you need to complete."
    ),
    "uk": (
        "Привіт, я Сем з AmtHero24. Я можу пояснити німецький лист, рахунок, договір або фото документа, "
        "а також знайти строк, суму, номер справи й потрібний наступний крок, якщо вони вказані. "
        "Також підготую офіційний лист, заперечення чи розірвання німецькою або впорядкую запис і процедуру крок за кроком. "
        "Надішли документ або коротко опиши справу."
    ),
    "el": (
        "Γεια, είμαι ο Sam από το AmtHero24. Μπορώ να εξηγήσω γερμανικές επιστολές, λογαριασμούς, συμβάσεις και φωτογραφίες εγγράφων, "
        "και να εντοπίσω προθεσμίες, ποσά, αριθμούς αναφοράς και το επόμενο απαιτούμενο βήμα όταν αναφέρονται. "
        "Μπορώ επίσης να συντάξω επίσημο email, ένσταση ή ακύρωση στα γερμανικά ή να οργανώσω ένα ραντεβού και τη διαδικασία βήμα βήμα. "
        "Στείλε το έγγραφο ή γράψε τι χρειάζεται να ολοκληρώσεις."
    ),
}

_LANGUAGE_ANSWERS = {
    "ar": "بحكي معك بالعربية، الألمانية، الإنجليزية، الأوكرانية واليونانية. غالبًا بكتشف لغتك من أول الرسائل وبكمّل فيها، وبتقدر تغيّرها بأي وقت.",
    "de": "Ich kann dir auf Deutsch, Arabisch, Englisch, Ukrainisch und Griechisch helfen. Meist erkenne ich deine Sprache automatisch und bleibe dabei; du kannst sie jederzeit wechseln.",
    "en": "I can help in German, Arabic, English, Ukrainian, and Greek. I usually detect your language automatically and continue in it; you can switch at any time.",
    "uk": "Я можу допомагати німецькою, арабською, англійською, українською та грецькою. Зазвичай я автоматично визначаю мову й продовжую нею; її можна змінити будь-коли.",
    "el": "Μπορώ να βοηθήσω στα γερμανικά, αραβικά, αγγλικά, ουκρανικά και ελληνικά. Συνήθως αναγνωρίζω αυτόματα τη γλώσσα σου και συνεχίζω σε αυτή· μπορείς να την αλλάξεις οποτεδήποτε.",
}

_CAPABILITY_ANSWERS = {
    "ar": "بساعدك تفهم الرسائل والفواتير والنماذج الألمانية، وبشرح الصور والمستندات ببساطة. كمان بكتب لك إيميلات واعتراضات وإلغاءات رسمية بالألماني، وتحتها شرح صغير بالعربي، وبمشي معك خطوة بخطوة بالإجراءات اليومية بألمانيا.",
    "de": "Ich erkläre deutsche Briefe, Rechnungen, Formulare, Bilder und Dokumente verständlich. Außerdem formuliere ich E-Mails, Widersprüche und Kündigungen auf Deutsch und begleite dich Schritt für Schritt bei alltäglichen Behördenthemen in Deutschland.",
    "en": "I explain German letters, invoices, forms, images, and documents in plain language. I can also draft formal German emails, objections, and cancellations, with a short explanation in your language, and guide you through everyday procedures in Germany.",
    "uk": "Я просто пояснюю німецькі листи, рахунки, форми, зображення й документи. Також можу підготувати офіційні листи, заперечення та розірвання німецькою з коротким поясненням українською і провести крок за кроком через побутові процедури в Німеччині.",
    "el": "Εξηγώ απλά γερμανικές επιστολές, λογαριασμούς, έντυπα, εικόνες και έγγραφα. Μπορώ επίσης να συντάξω επίσημα email, ενστάσεις και ακυρώσεις στα γερμανικά, με σύντομη εξήγηση στα ελληνικά, και να σε καθοδηγήσω βήμα βήμα σε καθημερινές διαδικασίες στη Γερμανία.",
}

_MORE_LANGUAGE_ANSWERS = {
    "ar": "هدول هنن اللغات الخمس المدعومة حاليًا: العربية، الألمانية، الإنجليزية، الأوكرانية واليونانية. قلّي بس بأي لغة بدك نكمّل.",
    "de": "Das sind aktuell die fünf unterstützten Sprachen: Deutsch, Arabisch, Englisch, Ukrainisch und Griechisch. Sag einfach, in welcher Sprache wir weitermachen sollen.",
    "en": "Those are the five currently supported languages: German, Arabic, English, Ukrainian, and Greek. Just tell me which one you prefer.",
    "uk": "Наразі підтримуються п’ять мов: німецька, арабська, англійська, українська та грецька. Просто скажи, якою продовжити.",
    "el": "Αυτές είναι οι πέντε γλώσσες που υποστηρίζονται τώρα: γερμανικά, αραβικά, αγγλικά, ουκρανικά και ελληνικά. Πες μου απλώς σε ποια θέλεις να συνεχίσουμε.",
}

_MORE_CAPABILITY_ANSWERS = {
    "ar": "كمان فيني أرتّب لك الخطوات، أطلع لك شو ناقص بالمستند، أجهّز رد مناسب، وأحافظ على نفس موضوع المحادثة حتى ما نعيد من الصفر. وإذا الموضوع قانوني أو حساس، بوضّح لك شو لازم تتأكد منه عند الجهة المختصة.",
    "de": "Ich kann außerdem Schritte ordnen, fehlende Angaben in Unterlagen erkennen, passende Antworten vorbereiten und beim selben Thema bleiben, ohne wieder von vorn anzufangen. Bei rechtlich sensiblen Fragen sage ich klar, was bei der zuständigen Stelle geprüft werden muss.",
    "en": "I can also organize the steps, spot missing information in a document, prepare a suitable reply, and keep the conversation on the same topic. For legal or sensitive matters, I clearly say what needs verification with the responsible authority.",
    "uk": "Я також можу впорядкувати кроки, помітити відсутні дані в документі, підготувати відповідь і продовжувати ту саму тему без початку з нуля. У юридично чутливих питаннях чітко скажу, що потрібно перевірити у відповідній установі.",
    "el": "Μπορώ επίσης να οργανώσω τα βήματα, να εντοπίσω ελλιπείς πληροφορίες σε έγγραφα, να ετοιμάσω κατάλληλη απάντηση και να συνεχίζω το ίδιο θέμα χωρίς να ξεκινάμε από την αρχή. Σε νομικά ή ευαίσθητα ζητήματα ξεκαθαρίζω τι πρέπει να επιβεβαιωθεί από την αρμόδια αρχή.",
}


def _contains_any(value: str, patterns: tuple[str, ...]) -> bool:
    return any(_normalize(pattern) in value for pattern in patterns)


def product_answer(text: str, language: str, previous_topic: str = "") -> tuple[str, str] | None:
    """Return an authoritative localized answer and topic for product questions."""
    normalized = _normalize(text)
    lang = language if language in SUPPORTED_LANGUAGES else "de"

    if normalized in {_normalize(item) for item in _GREETING_PATTERNS}:
        return _GREETING_ANSWERS[lang], "capabilities"
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
