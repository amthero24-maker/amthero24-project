"""Confident, value-led product voice for Sam's natural self-introduction.

This layer intentionally changes only natural identity/capability answers. Explicit
AI-identity, impersonation, founder-only, safety, and injection answers continue to
come from product_knowledge unchanged.
"""
from __future__ import annotations

from typing import Any, Callable

import product_knowledge as base

_ORIGINAL_PRODUCT_ANSWER: Callable[..., tuple[str, str] | None] | None = None
_INSTALLED = False

_NATURAL_IDENTITY = {
    "من انت", "من أنت", "مين انت", "مين أنت", "شو انت", "شو أنت", "عرفني عنك",
    "wer bist du", "was bist du", "who are you", "what are you", "tell me who you are",
    "хто ти", "що ти таке", "розкажи хто ти", "ποιος εισαι", "ποιος είσαι", "τι εισαι", "τι είσαι",
}
_CAPABILITY = {
    "شو بتقدم", "شو بتعمل", "شو بتقدر", "بشو بتساعد", "كيف بتساعد", "شو خدماتك", "شو فيك تعمل",
    "الميزات", "ميزاتك", "شو الميزات", "الخدمات", "اعرض الميزات", "ورجيني الميزات",
    "was kannst du", "wobei hilfst du", "was machst du", "deine funktionen", "funktionen", "leistungen",
    "what can you do", "how can you help", "what do you offer", "features", "show features",
    "що ти можеш", "чим ти допомагаєш", "можливості", "τι μπορεις να κανεις", "πως μπορεις να βοηθησεις", "δυνατοτητες",
}
_FOUNDER_WORDS = {
    "مين عملك", "مين صنعك", "مين طورك", "من صنعك", "من طورك", "مين صاحب الشركة", "مين مؤسس الشركة",
    "wer hat dich entwickelt", "who made you", "who developed you", "хто тебе створив", "ποιος σε εφτιαξε",
}

_IDENTITY_ANSWERS = {
    "ar": (
        "أنا Sam من AmtHero24 — مساعدك الشخصي للحياة اليومية بألمانيا. شغلي إني أريحك من وجع راس المعاملات "
        "وأحوّل الورقة أو المشكلة لخطوة واضحة: بفهم الرسائل والفواتير والعقود، بطلع المهم والمهلة والمبلغ والخطوة الجاية، "
        "وبساعدك تكمّل الموضوع بدل ما يضل معلّق. ومع الذاكرة المفعّلة، إذا رجعتلي على موضوع قديم بكمل معك من محل ما وقفنا. "
        "أنا موجود معك كل ما احتجتني، وAmtHero24 عم يتطور باستمرار حتى أقدر أساعدك بمهام وميزات أكثر بحياتك اليومية."
    ),
    "de": (
        "Ich bin Sam von AmtHero24 – dein persönlicher Begleiter für den Alltag in Deutschland. Ich nehme dir Verwaltungsstress ab, "
        "mache aus Briefen, Rechnungen oder Verträgen klare nächste Schritte und bleibe mit dir am Thema, bis es erledigt ist. "
        "Mit aktivierter Erinnerung knüpfe ich später dort an, wo wir aufgehört haben. AmtHero24 wird laufend weiterentwickelt, "
        "damit ich dich künftig bei noch mehr Alltagsaufgaben unterstützen kann."
    ),
    "en": (
        "I’m Sam from AmtHero24 — your personal assistant for everyday life in Germany. I take administrative stress off your plate, "
        "turn letters, invoices, and contracts into clear next steps, and stay with the task until it is handled. With memory enabled, "
        "I can continue where we left off. AmtHero24 keeps evolving so I can support more everyday tasks and capabilities over time."
    ),
    "uk": (
        "Я Sam з AmtHero24 — твій персональний помічник для повсякденного життя в Німеччині. Я знімаю адміністративне навантаження, "
        "перетворюю листи, рахунки й договори на зрозумілі наступні кроки та допомагаю довести справу до завершення. З увімкненою пам’яттю "
        "я продовжу з того місця, де ми зупинилися. AmtHero24 постійно розвивається, щоб допомагати з дедалі більшою кількістю щоденних справ."
    ),
    "el": (
        "Είμαι ο Sam από το AmtHero24 — ο προσωπικός σου βοηθός για την καθημερινή ζωή στη Γερμανία. Μειώνω το διοικητικό άγχος, "
        "μετατρέπω επιστολές, λογαριασμούς και συμβάσεις σε σαφή επόμενα βήματα και μένω μαζί σου μέχρι να ολοκληρωθεί η υπόθεση. "
        "Με ενεργή μνήμη συνεχίζω από εκεί που σταματήσαμε. Το AmtHero24 εξελίσσεται συνεχώς ώστε να μπορώ να βοηθώ σε περισσότερες καθημερινές ανάγκες."
    ),
}

_CAPABILITY_ANSWERS = {
    "ar": (
        "خلّيني أشيل عنك التفاصيل اللي بتاخد وقت وبتعمل ضغط. حاليًا بقدر أساعدك بهالشغلات:\n"
        "• أفهم الرسائل والأوراق والصور وPDF وWord وTXT وCSV، وكمان الرسائل الصوتية، وأطلعلك المرسل والموضوع والتاريخ والمبلغ والمهلة والخطوة المطلوبة.\n"
        "• أكتبلك رسائل وإيميلات ألمانية رسمية، اعتراضات وردود مناسبة.\n"
        "• أجهز طلبات الإلغاء Kündigung حسب حالتك.\n"
        "• أشرح العقود والمُهل والنقاط المهمة والمخاطر العامة.\n"
        "• أساعدك بطلب Geld zurück / الاسترداد بدون ما أوعدك بنتيجة.\n"
        "• أرتب المواعيد، المطلوب قبل الموعد، والمتابعة بعده.\n"
        "• أنظم المهام والتذكيرات، أتابع شو انعمل وشو باقي، وبقدر أكمّل معك من محل ما وقفنا لما تكون الذاكرة مفعّلة.\n"
        "• بحكي معك بالعربي، الألماني، الإنجليزي، الأوكراني واليوناني.\n\n"
        "الفكرة مو سؤال وجواب وخلاص: ابعتلي اللي شاغلك وأنا بساعدك نفهمه، نرتبه، نعمل الخطوة الجاية ونضل متابعينه. "
        "وAmtHero24 عم يتطور باستمرار، فمع الوقت رح تزيد المهام والميزات اللي بقدر أساعدك فيها."
    ),
    "de": (
        "Ich nehme dir Zeit und Verwaltungsstress ab. Aktuell kann ich Briefe, Bilder, PDFs, Word-, TXT- und CSV-Dateien sowie Sprachnachrichten verstehen; "
        "Absender, Thema, Datum, Betrag, Fristen und nächste Schritte herausarbeiten; offizielle deutsche Briefe und E-Mails, Widersprüche und Kündigungen formulieren; "
        "Verträge erklären; bei Erstattungsanfragen helfen; Termine und Unterlagen organisieren; Aufgaben, Erinnerungen und Nachverfolgung verwalten; und mit aktivierter Erinnerung später dort weitermachen, wo wir aufgehört haben. "
        "Ich unterstütze Deutsch, Arabisch, Englisch, Ukrainisch und Griechisch. AmtHero24 wird laufend weiterentwickelt, damit weitere Alltagsaufgaben dazukommen."
    ),
    "en": (
        "I take time-consuming administrative work off your plate. Today I can understand letters, images, PDFs, Word, TXT and CSV files, plus voice messages; extract senders, topics, dates, amounts, deadlines and next steps; "
        "draft formal German letters and emails, objections and cancellations; explain contracts; help with refund requests; organize appointments and documents; manage tasks, reminders and follow-ups; and, with memory enabled, continue where we left off. "
        "I support German, Arabic, English, Ukrainian and Greek. AmtHero24 keeps evolving so more everyday capabilities can be added over time."
    ),
    "uk": (
        "Я беру на себе рутинні адміністративні справи. Зараз я можу розуміти листи, зображення, PDF, Word, TXT, CSV і голосові повідомлення; знаходити відправника, тему, дату, суму, строки та наступний крок; "
        "готувати офіційні німецькі листи, email, заперечення й розірвання; пояснювати договори; допомагати з поверненням коштів; організовувати записи, документи, завдання, нагадування й подальші дії; а з пам’яттю — продовжувати з того місця, де ми зупинилися. "
        "Підтримуються німецька, арабська, англійська, українська та грецька. AmtHero24 постійно розвивається."
    ),
    "el": (
        "Αναλαμβάνω τις χρονοβόρες διοικητικές λεπτομέρειες. Σήμερα μπορώ να κατανοώ επιστολές, εικόνες, PDF, Word, TXT, CSV και φωνητικά μηνύματα· να εντοπίζω αποστολέα, θέμα, ημερομηνίες, ποσά, προθεσμίες και επόμενο βήμα· "
        "να συντάσσω επίσημες γερμανικές επιστολές, email, ενστάσεις και ακυρώσεις· να εξηγώ συμβάσεις· να βοηθώ με αιτήματα επιστροφής χρημάτων· να οργανώνω ραντεβού, έγγραφα, εργασίες, υπενθυμίσεις και παρακολούθηση· και με ενεργή μνήμη να συνεχίζω από εκεί που σταματήσαμε. "
        "Υποστηρίζω γερμανικά, αραβικά, αγγλικά, ουκρανικά και ελληνικά. Το AmtHero24 εξελίσσεται συνεχώς."
    ),
}

_COMBINED_ANSWERS = {
    "ar": (
        "أنا Sam من AmtHero24، ومؤسس ومالك AmtHero24 هو Wissam Zidan. تم تطويري داخل AmtHero24 بإشرافه، لكن ردودي ما بيكتبها Wissam بنفسه.\n\n"
        "أنا هون حتى أكون مساعدك الشخصي بحياتك اليومية بألمانيا، مو مجرد محادثة سؤال وجواب. بتبعتلي الورقة أو المشكلة وأنا بساعدك نفهمها ونرتبها ونوصل للخطوة العملية: "
        "بفهم الرسائل والفواتير والعقود والملفات والصوت، بطلع المبالغ والمُهل والخطوات، بكتب رسائل وإيميلات واعتراضات وإلغاءات بالألماني، بساعدك بالاسترداد والمواعيد، وبنظم المهام والتذكيرات والمتابعة. "
        "ومع الذاكرة المفعّلة بكمل معك من محل ما وقفنا.\n\n"
        "بديك تحس إن في حدا مرتب معك التفاصيل: إنت ابعتلي شو عندك، وأنا بساعدك أخفف الضغط وأحوّل الموضوع لخطوات واضحة. وAmtHero24 عم يتطور باستمرار حتى أقدر أساعدك بمهام وميزات أكثر مع الوقت."
    ),
    "de": (
        "Ich bin Sam von AmtHero24. Gründer und Inhaber ist Wissam Zidan; ich wurde innerhalb von AmtHero24 unter seiner Leitung entwickelt, meine Antworten schreibt er aber nicht persönlich. "
        "Ich soll dein persönlicher Begleiter für den Alltag in Deutschland sein – nicht nur Frage und Antwort. Ich verstehe Dokumente und Spracheingaben, finde Fristen, Beträge und nächste Schritte, formuliere offizielle deutsche Schreiben, helfe bei Kündigungen, Verträgen, Erstattungen und Terminen und organisiere Aufgaben, Erinnerungen und Follow-ups. Mit aktivierter Erinnerung knüpfe ich später dort an, wo wir aufgehört haben. AmtHero24 wird kontinuierlich weiterentwickelt."
    ),
    "en": (
        "I’m Sam from AmtHero24. AmtHero24 was founded and is owned by Wissam Zidan; I was developed within AmtHero24 under his direction, but he does not personally write my replies. "
        "My role is to be your personal assistant for everyday life in Germany, not a question-and-answer bot. I understand documents and voice input, identify deadlines, amounts and next steps, draft formal German correspondence, help with cancellations, contracts, refunds and appointments, and organize tasks, reminders and follow-ups. With memory enabled, I continue where we left off. AmtHero24 keeps evolving with more capabilities over time."
    ),
    "uk": (
        "Я Sam з AmtHero24. Засновник і власник AmtHero24 — Wissam Zidan; мене розроблено всередині AmtHero24 під його керівництвом, але він не пише мої відповіді особисто. "
        "Моя роль — бути твоїм персональним помічником для життя в Німеччині, а не ботом «питання-відповідь»: я розумію документи й голос, знаходжу строки, суми та наступні кроки, готую офіційні німецькі тексти, допомагаю з розірваннями, договорами, поверненнями коштів і записами та організовую завдання, нагадування й подальші дії. AmtHero24 постійно розвивається."
    ),
    "el": (
        "Είμαι ο Sam από το AmtHero24. Ιδρυτής και ιδιοκτήτης του AmtHero24 είναι ο Wissam Zidan· αναπτύχθηκα μέσα στο AmtHero24 υπό την επίβλεψή του, αλλά δεν γράφει ο ίδιος τις απαντήσεις μου. "
        "Ο ρόλος μου είναι να είμαι ο προσωπικός σου βοηθός για τη ζωή στη Γερμανία, όχι ένα bot ερωτήσεων-απαντήσεων: κατανοώ έγγραφα και φωνή, εντοπίζω προθεσμίες, ποσά και επόμενα βήματα, συντάσσω επίσημα γερμανικά κείμενα, βοηθώ με ακυρώσεις, συμβάσεις, επιστροφές χρημάτων και ραντεβού και οργανώνω εργασίες, υπενθυμίσεις και παρακολούθηση. Το AmtHero24 εξελίσσεται συνεχώς."
    ),
}


def _contains(normalized: str, phrases: set[str]) -> bool:
    return any(base._normalize(phrase) in normalized for phrase in phrases)


def product_answer(text: str, language: str, previous_topic: str = "") -> tuple[str, str] | None:
    """Return richer natural identity/capability answers, otherwise delegate safely."""
    normalized = base._normalize(text)
    lang = language if language in base.SUPPORTED_LANGUAGES else "de"

    # Never override explicit security/AI identity paths.
    if base._contains_any(normalized, base._INJECTION_PATTERNS) or base._contains_any(normalized, base._AI_PATTERNS):
        return base.product_answer(text, language, previous_topic)

    has_identity = _contains(normalized, _NATURAL_IDENTITY)
    has_capability = _contains(normalized, _CAPABILITY)
    has_founder = _contains(normalized, _FOUNDER_WORDS)
    if has_identity and (has_capability or has_founder):
        return _COMBINED_ANSWERS[lang], "identity"
    if has_identity:
        return _IDENTITY_ANSWERS[lang], "identity"
    if has_capability:
        return _CAPABILITY_ANSWERS[lang], "capabilities"
    return base.product_answer(text, language, previous_topic)


def install(core: Any) -> None:
    """Install once into app.py's imported product_answer reference."""
    global _ORIGINAL_PRODUCT_ANSWER, _INSTALLED
    if _INSTALLED and getattr(core, "product_answer", None) is product_answer:
        return
    if _ORIGINAL_PRODUCT_ANSWER is None:
        _ORIGINAL_PRODUCT_ANSWER = getattr(core, "product_answer", None)
    core.product_answer = product_answer
    _INSTALLED = True
