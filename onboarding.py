"""Deterministic first-run onboarding and memory-consent controls."""
from __future__ import annotations

import re
import unicodedata
from typing import Any

MEMORY_CONSENT_VERSION = "2026-07-v1"
SUPPORTED_LANGUAGES = {"de", "ar", "en", "uk", "el"}


def _lang(language: str) -> str:
    return language if language in SUPPORTED_LANGUAGES else "de"


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").casefold().strip()
    value = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", value)
    value = re.sub(r"[؟،؛!?.,:;]+", " ", value)
    value = re.sub(r"[^\w\u0600-\u06ff\u0370-\u03ff\u0400-\u04ff]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


_YES = {
    "نعم", "اي", "إي", "ايوه", "أيوه", "موافق", "فعّل", "فعل", "ok", "okay",
    "ja", "yes", "так", "ναι",
}
_NO = {
    "لا", "لأ", "مو هلق", "مش هلق", "بدون ذاكرة", "nein", "no", "ні", "όχι",
}
_ENABLE_MEMORY = {
    "فعل الذاكرة", "فعّل الذاكرة", "شغل الذاكرة", "تذكرني", "تذكر معلوماتي",
    "erinnerung aktivieren", "speicher aktivieren", "enable memory", "remember me",
    "увімкни пам'ять", "ενεργοποιησε μνημη",
}
_MEMORY_SUMMARY = {
    "شو بتعرف عني", "ماذا تعرف عني", "شو حافظ عني", "بياناتي المحفوظة",
    "was weißt du über mich", "was hast du gespeichert", "what do you know about me",
    "what have you saved", "що ти знаєш про мене", "τι ξερεις για μενα",
}
_SIMPLE_GREETINGS = {
    "مرحبا", "مرحباً", "اهلا", "أهلا", "هلا", "السلام عليكم", "سلام", "هاي",
    "hallo", "hi", "guten tag", "guten morgen", "guten abend",
    "hello", "hey", "привіт", "добрий день", "γεια", "καλημερα", "καλησπερα",
}


def consent_decision(text: str) -> bool | None:
    normalized = _normalize(text)
    if normalized in {_normalize(item) for item in _YES}:
        return True
    if normalized in {_normalize(item) for item in _NO}:
        return False
    return None


def is_enable_memory_request(text: str) -> bool:
    return _normalize(text) in {_normalize(item) for item in _ENABLE_MEMORY}


def is_memory_summary_request(text: str) -> bool:
    return _normalize(text) in {_normalize(item) for item in _MEMORY_SUMMARY}


def is_simple_greeting(text: str) -> bool:
    return _normalize(text) in {_normalize(item) for item in _SIMPLE_GREETINGS}


def welcome_message(language: str, name: str = "") -> str:
    lang = _lang(language)
    messages = {
        "ar": (
            "أهلًا 👋 أنا سام من AmtHero24. إذا ورقة، فاتورة أو موعد بألمانيا لخبطك، ابعتها إليّ وأنا بشرحلك شو معناها وشو الخطوة الجاية. "
            "بقدر كمان أكتبلك إيميلات، اعتراضات وإلغاءات رسمية بالألماني، وأرتّب معك الموضوع خطوة بخطوة بدون تعقيد. "
            + (f"تشرفت فيك يا {name}." if name else "قبل ما نبلّش، شو بتحب ناديلك؟")
        ),
        "de": (
            "Hallo 👋 Ich bin Sam von AmtHero24. Wenn dich ein Brief, eine Rechnung, ein Formular oder ein Termin in Deutschland verwirrt, schick ihn mir: Ich erkläre dir, was wichtig ist und was du als Nächstes tun kannst. "
            "Ich formuliere auch E-Mails, Widersprüche und Kündigungen auf Deutsch und begleite dich Schritt für Schritt. "
            + (f"Freut mich, dich kennenzulernen, {name}." if name else "Wie darf ich dich nennen?")
        ),
        "en": (
            "Hi 👋 I’m Sam from AmtHero24. If a German letter, invoice, form, or appointment is confusing, send it to me and I’ll explain what matters and the next practical step. "
            "I can also draft formal German emails, objections, and cancellations and guide you step by step. "
            + (f"Nice to meet you, {name}." if name else "What should I call you?")
        ),
        "uk": (
            "Привіт 👋 Я Сем з AmtHero24. Якщо німецький лист, рахунок, форма чи запис незрозумілі, надішли їх мені — я поясню головне та наступний практичний крок. "
            "Також можу підготувати офіційний лист, заперечення або розірвання німецькою і провести крок за кроком. "
            + (f"Радий знайомству, {name}." if name else "Як до тебе звертатися?")
        ),
        "el": (
            "Γεια 👋 Είμαι ο Sam από το AmtHero24. Αν ένα γερμανικό γράμμα, τιμολόγιο, έντυπο ή ραντεβού σε μπερδεύει, στείλ’ το μου και θα σου εξηγήσω τι έχει σημασία και ποιο είναι το επόμενο βήμα. "
            "Μπορώ επίσης να ετοιμάσω επίσημα email, ενστάσεις και ακυρώσεις στα γερμανικά και να σε καθοδηγήσω βήμα βήμα. "
            + (f"Χάρηκα, {name}." if name else "Πώς θέλεις να σε φωνάζω;")
        ),
    }
    return messages[lang]


def consent_prompt(language: str, name: str = "") -> str:
    lang = _lang(language)
    prefix = {
        "ar": f"تشرفت فيك{f' يا {name}' if name else ''} 🌿 ",
        "de": f"Freut mich{f', {name}' if name else ''} 🌿 ",
        "en": f"Nice to meet you{f', {name}' if name else ''} 🌿 ",
        "uk": f"Радий знайомству{f', {name}' if name else ''} 🌿 ",
        "el": f"Χάρηκα{f', {name}' if name else ''} 🌿 ",
    }[lang]
    body = {
        "ar": "إذا بتحب، فيني أتذكّر اسمك، لغتك، مدينتك والمواضيع اللي عم نتابعها حتى نكمل المرة الجاية من محل ما وقفنا. ما بحفظ كلمات سر، بيانات بنكية، أرقام هوية أو صور مستندات. هالشي اختياري، وبتقدر بأي وقت تسأل «شو بتعرف عني؟» أو تقول «امسح بياناتي». أفعّل الذاكرة؟ اكتب نعم أو لا.",
        "de": "Wenn du möchtest, kann ich mir deinen Namen, deine Sprache, deine Stadt und offene Themen merken, damit wir beim nächsten Mal nicht von vorn anfangen. Passwörter, Bankdaten, Ausweisnummern oder Dokumentbilder speichere ich nicht. Das ist freiwillig; du kannst jederzeit fragen „Was weißt du über mich?“ oder „Lösch meine Daten“. Soll ich die Erinnerung aktivieren? Antworte mit Ja oder Nein.",
        "en": "With your permission, I can remember your name, language, city, and open topics so we can continue next time without starting over. I do not store passwords, bank details, ID numbers, or document images. This is optional, and you can ask “What do you know about me?” or “Delete my data” at any time. Enable memory? Reply yes or no.",
        "uk": "За твоєю згодою я можу запам’ятати ім’я, мову, місто та відкриті теми, щоб наступного разу продовжити без початку з нуля. Я не зберігаю паролі, банківські дані, номери документів або зображення документів. Це добровільно; будь-коли можна запитати «Що ти знаєш про мене?» або сказати «Видали мої дані». Увімкнути пам’ять? Відповідай так або ні.",
        "el": "Με την άδειά σου μπορώ να θυμάμαι το όνομά σου, τη γλώσσα, την πόλη και τα ανοιχτά θέματα, ώστε την επόμενη φορά να συνεχίσουμε χωρίς να ξεκινήσουμε από την αρχή. Δεν αποθηκεύω κωδικούς, τραπεζικά στοιχεία, αριθμούς ταυτότητας ή εικόνες εγγράφων. Είναι προαιρετικό και μπορείς οποτεδήποτε να ρωτήσεις «Τι ξέρεις για μένα;» ή να πεις «Διέγραψε τα δεδομένα μου». Να ενεργοποιήσω τη μνήμη; Απάντησε ναι ή όχι.",
    }[lang]
    return prefix + body


def consent_granted_message(language: str, name: str = "") -> str:
    lang = _lang(language)
    return {
        "ar": f"تمام{f' يا {name}' if name else ''} ✅ فعّلت الذاكرة الآمنة. رح أتذكّر بس المعلومات المفيدة اللي بتساعدني أخدمك أحسن، وإنت المتحكم فيها دائمًا. شو أول شغلة بدك نحلها؟",
        "de": f"Alles klar{f', {name}' if name else ''} ✅ Die sichere Erinnerung ist aktiviert. Ich merke mir nur hilfreiche Angaben, und du behältst jederzeit die Kontrolle. Womit sollen wir anfangen?",
        "en": f"Done{f', {name}' if name else ''} ✅ Safe memory is enabled. I’ll remember only useful details, and you stay in control at all times. What should we tackle first?",
        "uk": f"Готово{f', {name}' if name else ''} ✅ Безпечну пам’ять увімкнено. Я зберігатиму лише корисні дані, а контроль завжди залишається у тебе. З чого почнемо?",
        "el": f"Έγινε{f', {name}' if name else ''} ✅ Η ασφαλής μνήμη ενεργοποιήθηκε. Θα θυμάμαι μόνο χρήσιμες πληροφορίες και ο έλεγχος παραμένει πάντα σε εσένα. Με τι ξεκινάμε;",
    }[lang]


def consent_declined_message(language: str) -> str:
    lang = _lang(language)
    return {
        "ar": "ولا يهمك 👍 منكمل بدون ذاكرة شخصية، وبساعدك بنفس الجودة ضمن هالمحادثة. إذا غيرت رأيك بأي وقت، قلّي «فعّل الذاكرة».",
        "de": "Kein Problem 👍 Wir machen ohne persönliche Erinnerung weiter; in diesem Gespräch helfe ich dir genauso. Wenn du es später ändern möchtest, schreib „Erinnerung aktivieren“.",
        "en": "No problem 👍 We’ll continue without personal memory, and I’ll help just as well in this conversation. You can say “enable memory” later if you change your mind.",
        "uk": "Без проблем 👍 Продовжимо без персональної пам’яті, а в цій розмові я допомагатиму так само. Якщо передумаєш, напиши «увімкни пам’ять».",
        "el": "Κανένα πρόβλημα 👍 Συνεχίζουμε χωρίς προσωπική μνήμη και θα σε βοηθήσω το ίδιο σε αυτή τη συζήτηση. Αν αλλάξεις γνώμη, γράψε «ενεργοποίησε μνήμη».",
    }[lang]


def ask_name_message(language: str) -> str:
    lang = _lang(language)
    return {
        "ar": "أكيد، بس شو بتحب ناديلك؟ فيك تكتب اسمك الأول فقط.",
        "de": "Gern — wie darf ich dich nennen? Dein Vorname reicht.",
        "en": "Sure — what should I call you? Your first name is enough.",
        "uk": "Звісно — як до тебе звертатися? Достатньо імені.",
        "el": "Βεβαίως — πώς θέλεις να σε φωνάζω; Αρκεί το μικρό σου όνομα.",
    }[lang]


def memory_summary_message(language: str, profile: dict[str, Any]) -> str:
    lang = _lang(language)
    if profile.get("memory_consent") != "granted":
        return {
            "ar": "الذاكرة الشخصية مو مفعّلة حاليًا. إذا بتحب فعّلها، قلّي «فعّل الذاكرة».",
            "de": "Die persönliche Erinnerung ist derzeit nicht aktiviert. Wenn du möchtest, schreib „Erinnerung aktivieren“.",
            "en": "Personal memory is not enabled right now. Say “enable memory” if you want to turn it on.",
            "uk": "Персональну пам’ять зараз не увімкнено. Напиши «увімкни пам’ять», якщо хочеш її активувати.",
            "el": "Η προσωπική μνήμη δεν είναι ενεργή. Γράψε «ενεργοποίησε μνήμη» αν θέλεις να την ενεργοποιήσεις.",
        }[lang]

    facts: list[str] = []
    labels = {
        "ar": {"first_name": "اسمك", "preferred_language": "لغتك", "city": "مدينتك", "current_topic": "الموضوع الحالي"},
        "de": {"first_name": "Name", "preferred_language": "Sprache", "city": "Stadt", "current_topic": "aktuelles Thema"},
        "en": {"first_name": "name", "preferred_language": "language", "city": "city", "current_topic": "current topic"},
        "uk": {"first_name": "ім’я", "preferred_language": "мова", "city": "місто", "current_topic": "поточна тема"},
        "el": {"first_name": "όνομα", "preferred_language": "γλώσσα", "city": "πόλη", "current_topic": "τρέχον θέμα"},
    }[lang]
    for key in ("first_name", "preferred_language", "city", "current_topic"):
        value = str(profile.get(key) or "").strip()
        if value:
            facts.append(f"{labels[key]}: {value}")
    if not facts:
        return {
            "ar": "الذاكرة مفعّلة، بس ما عندي عنك معلومات شخصية مفيدة محفوظة لسا.",
            "de": "Die Erinnerung ist aktiviert, aber bisher sind keine hilfreichen persönlichen Angaben gespeichert.",
            "en": "Memory is enabled, but no useful personal details have been saved yet.",
            "uk": "Пам’ять увімкнено, але корисних персональних даних поки не збережено.",
            "el": "Η μνήμη είναι ενεργή, αλλά δεν έχουν αποθηκευτεί ακόμη χρήσιμα προσωπικά στοιχεία.",
        }[lang]
    intro = {
        "ar": "المعلومات المفيدة المحفوظة عندي:",
        "de": "Diese hilfreichen Angaben sind gespeichert:",
        "en": "Here is the useful information I have saved:",
        "uk": "Ось корисні дані, які збережено:",
        "el": "Αυτές είναι οι χρήσιμες πληροφορίες που έχω αποθηκεύσει:",
    }[lang]
    return intro + "\n- " + "\n- ".join(facts)
