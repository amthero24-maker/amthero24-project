"""Warm conversational layer for Sam's identity and capability turns.

Keeps explicit AI/security identity handling in the existing product knowledge path,
while making ordinary introductions shorter, warmer, and interactive.
"""
from __future__ import annotations

from typing import Any

import product_knowledge as knowledge
import sam_product_voice as previous

_INSTALLED = False

_NAME_PATTERNS = {
    "wie heißt du", "wie heisst du", "wie heißt du denn", "wie heisst du denn",
    "what is your name", "what's your name", "whats your name",
    "شو اسمك", "شو اسمك انت", "اسمك شو", "اسمك", "ما اسمك",
    "як тебе звати", "ποιο ειναι το ονομα σου", "πως σε λενε",
}
_IDENTITY_PATTERNS = {
    "من انت", "من أنت", "مين انت", "مين أنت", "عرفني عنك",
    "wer bist du", "was bist du", "who are you", "tell me who you are",
    "хто ти", "розкажи хто ти", "ποιος εισαι", "ποιος είσαι",
}
_CAPABILITY_PATTERNS = {
    "شو بتعمل", "شو خدماتك", "شو ميزاتك", "شو خدماتك وميزاتك كلها", "شو فيك تعمل", "بشو بتساعد",
    "was kannst du", "was machst du", "wobei hilfst du", "deine funktionen",
    "what can you do", "how can you help", "what do you offer", "features",
    "що ти можеш", "чим ти допомагаєш", "τι μπορεις να κανεις", "πως μπορεις να βοηθησεις",
}

_NAME = {
    "ar": "اسمي Sam 🙂 وأنا من AmtHero24. وإذا بدك، اعتبرني مساعدك الشخصي هون على واتساب. شو أكتر شغلة عادةً بتاخد من وقتك أو بتعملك وجع راس؟",
    "de": "Ich heiße Sam 🙂 Ich bin von AmtHero24 und soll dir im Alltag wirklich Arbeit abnehmen, nicht nur Fragen beantworten. Was kostet dich normalerweise am meisten Zeit oder Nerven?",
    "en": "I’m Sam 🙂 I’m from AmtHero24, and I’m here to take real everyday work off your plate, not just answer questions. What usually costs you the most time or stress?",
    "uk": "Мене звати Sam 🙂 Я з AmtHero24 і хочу реально знімати з тебе повсякденні справи, а не просто відповідати на запитання. Що зазвичай забирає найбільше часу або нервів?",
    "el": "Με λένε Sam 🙂 Είμαι από το AmtHero24 και είμαι εδώ για να σου αφαιρώ πραγματικό καθημερινό βάρος, όχι απλώς να απαντώ σε ερωτήσεις. Τι σου τρώει συνήθως περισσότερο χρόνο ή άγχος;",
}
_IDENTITY = {
    "ar": "أنا Sam من AmtHero24 — مساعدك الشخصي للحياة اليومية بألمانيا. بتبعتلي الورقة أو المشكلة وأنا بساعدك نفهمها، نرتبها ونوصل للخطوة الجاية بدون ما تضل شايلها براسك لحالك. وبقدر أتابع معك التذكيرات والمهام والمواضيع القديمة لما تكون الذاكرة مفعّلة. شو بتحب أريحك منه أول شي؟",
    "de": "Ich bin Sam von AmtHero24 — dein persönlicher Begleiter für den Alltag in Deutschland. Du gibst mir den Brief, Vertrag, Termin oder das Problem, und ich helfe dir, daraus einen klaren nächsten Schritt zu machen und dranzubleiben. Wobei soll ich dir als Erstes Arbeit abnehmen?",
    "en": "I’m Sam from AmtHero24 — your personal assistant for everyday life in Germany. Give me the letter, contract, appointment, or problem and I’ll help turn it into a clear next step and keep the follow-up organized. What would you like me to take off your plate first?",
    "uk": "Я Sam з AmtHero24 — твій персональний помічник для життя в Німеччині. Надішли лист, договір, запис або проблему, і я допоможу перетворити це на зрозумілий наступний крок та не загубити продовження. З чого хочеш почати?",
    "el": "Είμαι ο Sam από το AmtHero24 — ο προσωπικός σου βοηθός για την καθημερινή ζωή στη Γερμανία. Στείλε μου την επιστολή, τη σύμβαση, το ραντεβού ή το πρόβλημα και θα σε βοηθήσω να το μετατρέψουμε σε καθαρό επόμενο βήμα και να το παρακολουθήσουμε. Από τι θέλεις να ξεκινήσουμε;",
}
_CAPABILITIES = {
    "ar": "حاليًا بقدر أريحك من شغلات كتيرة: أفهم رسائل وأوراق وصور وPDF وWord وTXT وCSV ورسائل صوتية، وأطلع المهم والمهلة والمبلغ والخطوة الجاية؛ أكتب رسائل وإيميلات واعتراضات وإلغاءات رسمية بالألماني؛ أشرح العقود؛ أساعد بطلبات الاسترداد؛ أرتب المواعيد والمستندات؛ وأنظم المهام والتذكيرات والمتابعة. وبحكي عربي وألماني وإنجليزي وأوكراني ويوناني. AmtHero24 عم يتطور باستمرار حتى أقدر أساعدك بمهام أكتر. بدل ما نشرح نظري: ابعتلي شغلة حقيقية عندك ونشتغل عليها سوا.",
    "de": "Aktuell kann ich Briefe, Bilder, PDFs, Word-, TXT- und CSV-Dateien sowie Sprachnachrichten verstehen; Fristen, Beträge und nächste Schritte herausarbeiten; offizielle deutsche E-Mails, Widersprüche und Kündigungen formulieren; Verträge erklären; bei Erstattungen helfen; Termine und Unterlagen organisieren; sowie Aufgaben, Erinnerungen und Follow-ups verwalten. Ich unterstütze Deutsch, Arabisch, Englisch, Ukrainisch und Griechisch. AmtHero24 entwickelt sich weiter. Am besten testen wir es direkt: Schick mir etwas Echtes, das du gerade erledigen musst.",
    "en": "Today I can understand letters, images, PDFs, Word, TXT and CSV files, plus voice messages; extract deadlines, amounts and next steps; draft formal German emails, objections and cancellations; explain contracts; help with refunds; organize appointments and documents; and manage tasks, reminders and follow-ups. I support German, Arabic, English, Ukrainian and Greek, and AmtHero24 keeps evolving. Best way to see it: send me one real thing you need to get done.",
    "uk": "Зараз я можу розуміти листи, зображення, PDF, Word, TXT, CSV і голосові повідомлення; знаходити строки, суми та наступні кроки; готувати офіційні німецькі листи, заперечення й розірвання; пояснювати договори; допомагати з поверненням коштів; організовувати записи, документи, завдання, нагадування й подальші дії. Підтримую п’ять мов і AmtHero24 постійно розвивається. Найкраще — надішли реальну справу, яку треба вирішити зараз.",
    "el": "Σήμερα μπορώ να κατανοώ επιστολές, εικόνες, PDF, Word, TXT, CSV και φωνητικά μηνύματα· να εντοπίζω προθεσμίες, ποσά και επόμενα βήματα· να συντάσσω επίσημα γερμανικά email, ενστάσεις και ακυρώσεις· να εξηγώ συμβάσεις· να βοηθώ με επιστροφές χρημάτων· να οργανώνω ραντεβού, έγγραφα, εργασίες, υπενθυμίσεις και παρακολούθηση. Υποστηρίζω πέντε γλώσσες και το AmtHero24 συνεχίζει να εξελίσσεται. Στείλε μου κάτι πραγματικό που πρέπει να τακτοποιήσεις τώρα.",
}


def _has(value: str, patterns: set[str]) -> bool:
    return any(knowledge._normalize(item) in value for item in patterns)


def product_answer(text: str, language: str, previous_topic: str = ""):
    normalized = knowledge._normalize(text)
    lang = language if language in knowledge.SUPPORTED_LANGUAGES else "de"

    if knowledge._contains_any(normalized, knowledge._INJECTION_PATTERNS) or knowledge._contains_any(normalized, knowledge._AI_PATTERNS):
        return previous.product_answer(text, language, previous_topic)

    if _has(normalized, _NAME_PATTERNS):
        return _NAME[lang], "identity"
    if _has(normalized, _IDENTITY_PATTERNS) and not _has(normalized, _CAPABILITY_PATTERNS):
        return _IDENTITY[lang], "identity"
    if _has(normalized, _CAPABILITY_PATTERNS):
        return _CAPABILITIES[lang], "capabilities"
    return previous.product_answer(text, language, previous_topic)


def install(core: Any) -> None:
    global _INSTALLED
    core.product_answer = product_answer
    _INSTALLED = True
