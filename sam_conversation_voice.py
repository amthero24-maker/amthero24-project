"""Warm, brand-led conversational answers for Sam's identity and capabilities.

Ordinary introductions should feel personal, confident, useful, and easy to imagine in
real life. Direct questions about AI/human identity, impersonation, founder facts, or
system instructions remain delegated to the existing authoritative safety boundary.
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
    "شو بتقدر", "شو بتقدر تساعدني", "شو بتقدر تساعدني فيه", "شو فيك تساعدني",
    "كيف بتساعد", "كيف بتقدر تساعدني", "بماذا تساعدني", "كيف ممكن تساعدني",
    "was kannst du", "was kannst du für mich tun", "wie kannst du mir helfen",
    "wie kannst du mir in deutschland helfen", "wobei kannst du mir in deutschland helfen",
    "was machst du", "wobei hilfst du", "deine funktionen",
    "what can you do", "what can you help me with", "how can you help me", "what do you offer", "features",
    "що ти можеш", "чим ти допомагаєш", "як ти можеш допомогти",
    "τι μπορεις να κανεις", "πως μπορεις να βοηθησεις", "με τι μπορεις να βοηθησεις",
}

_NAME = {
    "ar": (
        "اسمي Sam 🙂 وأنا من AmtHero24. طوّرتني AmtHero24 بأحدث التقنيات حتى أكون مساعدك الشخصي "
        "بالحياة اليومية بألمانيا وأخفف عنك ضغط الأوراق والمعاملات. شو أول شغلة بدك نبلش فيها؟"
    ),
    "de": (
        "Ich heiße Sam 🙂 AmtHero24 hat mich mit moderner Technologie entwickelt, damit ich dir im Alltag "
        "in Deutschland Arbeit abnehme und Papierkram leichter mache. Womit sollen wir anfangen?"
    ),
    "en": (
        "I’m Sam 🙂 AmtHero24 developed me with modern technology to take everyday administrative work off "
        "your plate in Germany. What should we start with?"
    ),
    "uk": (
        "Мене звати Sam 🙂 AmtHero24 створив мене на основі сучасних технологій, щоб полегшувати твої "
        "повсякденні справи й бюрократію в Німеччині. З чого почнемо?"
    ),
    "el": (
        "Με λένε Sam 🙂 Το AmtHero24 με ανέπτυξε με σύγχρονη τεχνολογία για να σου αφαιρώ καθημερινή "
        "γραφειοκρατία και άγχος στη Γερμανία. Από τι ξεκινάμε;"
    ),
}

_IDENTITY = {
    "ar": (
        "أنا Sam من AmtHero24. طوّرتني AmtHero24 بأحدث التقنيات حتى أكون مساعدك الشخصي بالحياة اليومية "
        "بألمانيا وأخفف عنك وجع راس الأوراق والمعاملات. إنت ابعتلي اللي شاغلك، وأنا برتبه معك لخطوات واضحة "
        "وبضل متابع معك. شو أول شغلة بدك نبلش فيها؟"
    ),
    "de": (
        "Ich bin Sam von AmtHero24. AmtHero24 hat mich mit moderner Technologie entwickelt, damit ich dir "
        "im Alltag in Deutschland Verwaltungsarbeit abnehme, Dinge ordne und mit dir am Thema dranbleibe. "
        "Womit soll ich dir als Erstes helfen?"
    ),
    "en": (
        "I’m Sam from AmtHero24. AmtHero24 developed me with modern technology to make everyday life in "
        "Germany easier, organize administrative tasks, and keep the follow-up from getting lost. What should "
        "we start with?"
    ),
    "uk": (
        "Я Sam з AmtHero24. AmtHero24 створив мене на основі сучасних технологій, щоб полегшувати твоє "
        "повсякденне життя в Німеччині, впорядковувати адміністративні справи й не губити продовження. "
        "З чого хочеш почати?"
    ),
    "el": (
        "Είμαι ο Sam από το AmtHero24. Το AmtHero24 με ανέπτυξε με σύγχρονη τεχνολογία για να κάνω πιο εύκολη "
        "την καθημερινότητά σου στη Γερμανία, να οργανώνω τις διοικητικές σου υποθέσεις και να μη χάνεται η "
        "συνέχεια. Από τι θέλεις να ξεκινήσουμε;"
    ),
}

_CAPABILITIES = {
    "ar": (
        "خلّيني أعطيك مثال قريب من حياتك: وصلك بريد وما فهمت شو بدهم؟ ابعته إلي، وأنا بطلعلك المهم والمهلة "
        "والمبلغ والخطوة الجاية، وبجهزلك الرد أو الإيميل بالألماني إذا احتجت. وبساعدك كمان بالإلغاءات، شرح "
        "العقود، استرجاع المال، المواعيد والأوراق المطلوبة، وتنظيم المهام والتذكيرات والمتابعة — حتى ما يضل "
        "شي مهم ضايع أو معلّق براسك. إنت ابعتلي اللي شاغلك، وأنا برتبه معك. شو أول شغلة بدك نبلش فيها؟"
    ),
    "de": (
        "Ein Beispiel aus dem Alltag: Du bekommst einen Brief und weißt nicht, was verlangt wird. Schick ihn "
        "mir, und ich ordne das Wichtige, Fristen, Beträge und den nächsten Schritt. Wenn du antworten musst, "
        "formuliere ich die deutsche Nachricht oder E-Mail. Außerdem helfe ich bei Kündigungen, Verträgen, "
        "Erstattungen, Terminen und Unterlagen und organisiere Aufgaben, Erinnerungen und Follow-ups. Was liegt "
        "bei dir gerade auf dem Tisch?"
    ),
    "en": (
        "Here’s a real-life example: a letter arrives and you are not sure what it wants. Send it to me and I’ll "
        "organize the key point, deadline, amount, and next step. If you need to reply, I can prepare the German "
        "message or email. I also help with cancellations, contracts, refunds, appointments and required documents, "
        "plus tasks, reminders, and follow-ups. What is the first thing you want to sort out?"
    ),
    "uk": (
        "Ось простий життєвий приклад: приходить лист, а ти не розумієш, що від тебе хочуть. Надішли його "
        "мені — я виділю головне, строк, суму та наступний крок, а за потреби підготую відповідь або email "
        "німецькою. Також допомагаю з розірваннями, договорами, поверненням коштів, записами й потрібними "
        "документами, а ще організовую завдання, нагадування та подальші дії. З чого почнемо?"
    ),
    "el": (
        "Ένα καθημερινό παράδειγμα: λαμβάνεις μια επιστολή και δεν ξέρεις τι ζητά. Στείλε τη σε μένα και θα "
        "οργανώσω το βασικό σημείο, την προθεσμία, το ποσό και το επόμενο βήμα. Αν χρειάζεται απάντηση, μπορώ "
        "να ετοιμάσω το γερμανικό μήνυμα ή email. Βοηθώ επίσης με ακυρώσεις, συμβάσεις, επιστροφές χρημάτων, "
        "ραντεβού και απαιτούμενα έγγραφα, καθώς και με εργασίες, υπενθυμίσεις και παρακολούθηση. Από τι ξεκινάμε;"
    ),
}

_COMBINED = {
    "ar": (
        "أنا Sam من AmtHero24. طوّرتني AmtHero24 بأحدث التقنيات حتى أكون مساعدك الشخصي بالحياة اليومية "
        "بألمانيا وأخفف عنك وجع راس الأوراق والمعاملات.\n\n"
        "وصلك بريد وما فهمت شو بدهم؟ ابعته إلي، وأنا بطلعلك المهم والمهلة والخطوة الجاية، وبجهزلك الرد "
        "بالألماني إذا احتجت. وبساعدك كمان بالإلغاءات، شرح العقود، استرجاع المال، المواعيد والأوراق المطلوبة، "
        "وتنظيم المهام والتذكيرات والمتابعة — حتى ما يضل شي مهم ضايع أو معلّق براسك.\n\n"
        "إنت ابعتلي اللي شاغلك، وأنا برتبه معك. شو أول شغلة بدك نبلش فيها؟"
    ),
    "de": (
        "Ich bin Sam von AmtHero24. AmtHero24 hat mich mit moderner Technologie entwickelt, damit ich dir im "
        "Alltag in Deutschland Papierkram und Verwaltungsstress abnehme.\n\n"
        "Du bekommst einen Brief und weißt nicht, was verlangt wird? Schick ihn mir. Ich ordne das Wichtige, "
        "Fristen, Beträge und den nächsten Schritt und formuliere bei Bedarf die deutsche Antwort. Dazu helfe ich "
        "bei Kündigungen, Verträgen, Erstattungen, Terminen und Unterlagen und organisiere Aufgaben, Erinnerungen "
        "und Follow-ups.\n\nWomit sollen wir anfangen?"
    ),
    "en": (
        "I’m Sam from AmtHero24. AmtHero24 developed me with modern technology to take paperwork and everyday "
        "administrative stress off your plate in Germany.\n\n"
        "A letter arrives and you are not sure what it wants? Send it to me. I’ll organize the key point, deadline, "
        "amount, and next step and prepare the German reply when needed. I also help with cancellations, contracts, "
        "refunds, appointments and required documents, plus tasks, reminders, and follow-ups.\n\n"
        "What should we start with?"
    ),
    "uk": (
        "Я Sam з AmtHero24. AmtHero24 створив мене на основі сучасних технологій, щоб знімати з тебе паперову "
        "роботу й повсякденний адміністративний стрес у Німеччині.\n\n"
        "Прийшов лист і незрозуміло, що від тебе хочуть? Надішли його мені. Я виділю головне, строк, суму та "
        "наступний крок і за потреби підготую відповідь німецькою. Також допомагаю з розірваннями, договорами, "
        "поверненням коштів, записами й потрібними документами, а ще з завданнями, нагадуваннями та подальшими "
        "діями.\n\nЗ чого почнемо?"
    ),
    "el": (
        "Είμαι ο Sam από το AmtHero24. Το AmtHero24 με ανέπτυξε με σύγχρονη τεχνολογία για να σου αφαιρώ τη "
        "γραφειοκρατία και το καθημερινό διοικητικό άγχος στη Γερμανία.\n\n"
        "Έλαβες μια επιστολή και δεν ξέρεις τι ζητά; Στείλε τη σε μένα. Θα οργανώσω το βασικό σημείο, την "
        "προθεσμία, το ποσό και το επόμενο βήμα και, αν χρειάζεται, θα ετοιμάσω τη γερμανική απάντηση. Βοηθώ "
        "επίσης με ακυρώσεις, συμβάσεις, επιστροφές χρημάτων, ραντεβού και απαιτούμενα έγγραφα, καθώς και με "
        "εργασίες, υπενθυμίσεις και παρακολούθηση.\n\nΑπό τι ξεκινάμε;"
    ),
}


def _has(value: str, patterns: set[str]) -> bool:
    return any(knowledge._normalize(item) in value for item in patterns)


def product_answer(text: str, language: str, previous_topic: str = ""):
    normalized = knowledge._normalize(text)
    lang = language if language in knowledge.SUPPORTED_LANGUAGES else "de"

    if (
        knowledge._contains_any(normalized, knowledge._INJECTION_PATTERNS)
        or knowledge._contains_any(normalized, knowledge._AI_PATTERNS)
        or knowledge._contains_any(normalized, knowledge._FOUNDER_PATTERNS)
    ):
        return previous.product_answer(text, language, previous_topic)

    has_name = _has(normalized, _NAME_PATTERNS)
    has_identity = _has(normalized, _IDENTITY_PATTERNS)
    has_capability = _has(normalized, _CAPABILITY_PATTERNS)

    if has_capability and (has_identity or has_name):
        return _COMBINED[lang], "capabilities"
    if has_name:
        return _NAME[lang], "identity"
    if has_identity:
        return _IDENTITY[lang], "identity"
    if has_capability:
        return _CAPABILITIES[lang], "capabilities"
    return previous.product_answer(text, language, previous_topic)


def install(core: Any) -> None:
    global _INSTALLED
    core.product_answer = product_answer
    _INSTALLED = True
