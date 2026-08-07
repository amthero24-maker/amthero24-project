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
_NAME_QUESTIONS = {
    "شو اسمي", "ما اسمي", "بتعرف اسمي", "بتتذكر اسمي", "شو بتناديني",
    "wie heiße ich", "wie ist mein name", "kennst du meinen namen",
    "what is my name", "do you know my name", "do you remember my name",
    "як мене звати", "ти пам ятаєш моє ім я",
    "πως με λενε", "θυμασαι το ονομα μου",
}
_SIMPLE_GREETINGS = {
    "مرحبا", "مرحباً", "اهلا", "أهلا", "هلا", "السلام عليكم", "سلام", "هاي",
    "hallo", "hi", "guten tag", "guten morgen", "guten abend",
    "hello", "hey", "привіт", "добрий день", "γεια", "καλημερα", "καλησπερα",
}

_LANGUAGE_NAMES = {
    "ar": {"ar": "العربية", "de": "الألمانية", "en": "الإنجليزية", "uk": "الأوكرانية", "el": "اليونانية"},
    "de": {"ar": "Arabisch", "de": "Deutsch", "en": "Englisch", "uk": "Ukrainisch", "el": "Griechisch"},
    "en": {"ar": "Arabic", "de": "German", "en": "English", "uk": "Ukrainian", "el": "Greek"},
    "uk": {"ar": "арабська", "de": "німецька", "en": "англійська", "uk": "українська", "el": "грецька"},
    "el": {"ar": "Αραβικά", "de": "Γερμανικά", "en": "Αγγλικά", "uk": "Ουκρανικά", "el": "Ελληνικά"},
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


def is_name_question(text: str) -> bool:
    return _normalize(text) in {_normalize(item) for item in _NAME_QUESTIONS}


def is_simple_greeting(text: str) -> bool:
    return _normalize(text) in {_normalize(item) for item in _SIMPLE_GREETINGS}


def _display_language(value: str, language: str) -> str:
    safe = _lang(language)
    return _LANGUAGE_NAMES[safe].get((value or "").strip(), (value or "").strip())


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
        "ar": "أكيد 🌿 شو بتحب ناديلك؟ اكتب اسمك الأول بس، وبعدها بسألك إذا بتحب أحفظه للمرة الجاية.",
        "de": "Gern 🌿 Wie darf ich dich nennen? Dein Vorname reicht; danach frage ich, ob ich ihn fürs nächste Mal speichern darf.",
        "en": "Sure 🌿 What should I call you? Your first name is enough; then I’ll ask whether I may remember it for next time.",
        "uk": "Звісно 🌿 Як до тебе звертатися? Достатньо імені; потім я запитаю, чи можна запам’ятати його на майбутнє.",
        "el": "Βεβαίως 🌿 Πώς θέλεις να σε φωνάζω; Αρκεί το μικρό σου όνομα· μετά θα ρωτήσω αν μπορώ να το θυμάμαι για την επόμενη φορά.",
    }[lang]


def saved_name_message(language: str, name: str) -> str:
    lang = _lang(language)
    return {
        "ar": f"إي يا {name}، متذكّرك 🌿 اسمك {name}. ما بدك تعرّفني عن حالك من جديد كل مرة؛ إذا رجعنا لموضوع قديم منكمّل من محل ما وقفنا.",
        "de": f"Ja, {name} — ich erinnere mich an dich 🌿 Du heißt {name}. Du musst dich hier nicht jedes Mal neu vorstellen; bei einem alten Thema machen wir dort weiter, wo wir aufgehört haben.",
        "en": f"Yes, {name} — I remember you 🌿 Your name is {name}. You do not need to introduce yourself again each time; when we return to an old topic, we can continue where we left off.",
        "uk": f"Так, {name}, я тебе пам’ятаю 🌿 Тебе звати {name}. Не потрібно щоразу знайомитися заново; до старої справи можемо повернутися з того місця, де зупинилися.",
        "el": f"Ναι, {name}, σε θυμάμαι 🌿 Σε λένε {name}. Δεν χρειάζεται να συστηνόμαστε από την αρχή κάθε φορά· σε παλιό θέμα συνεχίζουμε από εκεί που μείναμε.",
    }[lang]


def pending_name_message(language: str, name: str) -> str:
    lang = _lang(language)
    return {
        "ar": f"قلتلي ناديلك {name}، بس لسا ما حفظته بشكل دائم. إذا بدك أتذكّره للمرة الجاية، جاوب «نعم» على سؤال الذاكرة؛ وإذا لا، جاوب «لا».",
        "de": f"Du hast mir gesagt, dass ich dich {name} nennen soll, aber dauerhaft gespeichert ist es noch nicht. Antworte auf die Erinnerungsfrage mit Ja oder Nein.",
        "en": f"You told me to call you {name}, but it is not stored permanently yet. Reply yes or no to the memory question.",
        "uk": f"Ти сказав, що до тебе звертатися {name}, але це ще не збережено надовго. Відповідай так або ні на запит про пам’ять.",
        "el": f"Μου είπες να σε φωνάζω {name}, αλλά δεν έχει αποθηκευτεί μόνιμα. Απάντησε ναι ή όχι στην ερώτηση για τη μνήμη.",
    }[lang]


def memory_summary_message(language: str, profile: dict[str, Any]) -> str:
    lang = _lang(language)
    if profile.get("memory_consent") != "granted":
        return {
            "ar": "حاليًا ما عندي معلومات شخصية محفوظة عنك. إذا بتحب نبلّش صح، شو بتحب ناديلك؟ بعد الاسم بسألك بوضوح إذا بدك أحفظه للمرة الجاية.",
            "de": "Aktuell habe ich keine persönlichen Angaben über dich gespeichert. Wenn du möchtest, beginnen wir mit deinem Vornamen; danach frage ich klar, ob ich ihn fürs nächste Mal speichern darf.",
            "en": "I do not currently have personal information saved about you. We can start with your first name, and then I’ll clearly ask whether I may remember it for next time.",
            "uk": "Зараз у мене немає збережених персональних даних про тебе. Можемо почати з імені, а потім я чітко запитаю дозвіл зберегти його на майбутнє.",
            "el": "Αυτή τη στιγμή δεν έχω αποθηκευμένες προσωπικές πληροφορίες για εσένα. Μπορούμε να ξεκινήσουμε με το μικρό σου όνομα και μετά θα ζητήσω καθαρά άδεια για να το θυμάμαι.",
        }[lang]

    facts: list[str] = []
    labels = {
        "ar": {"first_name": "اسمك", "preferred_language": "لغتك", "city": "مدينتك", "current_topic": "آخر موضوع عم نتابعه"},
        "de": {"first_name": "Name", "preferred_language": "Sprache", "city": "Stadt", "current_topic": "letztes offenes Thema"},
        "en": {"first_name": "name", "preferred_language": "language", "city": "city", "current_topic": "last open topic"},
        "uk": {"first_name": "ім’я", "preferred_language": "мова", "city": "місто", "current_topic": "остання відкрита тема"},
        "el": {"first_name": "όνομα", "preferred_language": "γλώσσα", "city": "πόλη", "current_topic": "τελευταίο ανοιχτό θέμα"},
    }[lang]
    for key in ("first_name", "preferred_language", "city", "current_topic"):
        value = str(profile.get(key) or "").strip()
        if not value:
            continue
        if key == "preferred_language":
            value = _display_language(value, lang)
        facts.append(f"{labels[key]}: {value}")

    if not facts:
        return {
            "ar": "الذاكرة مفعّلة 🌿 بس لسا ما صار بيناتنا شي مفيد لازم أتذكّره. أول ما يكون في اسم، مدينة أو موضوع عم نتابعه، بخليه معي حتى ما نرجع من الصفر.",
            "de": "Die Erinnerung ist aktiv 🌿, aber bisher gibt es noch nichts Nützliches, das ich für unsere Fortsetzung behalten muss. Sobald Name, Stadt oder ein offenes Thema relevant sind, kann ich daran anknüpfen.",
            "en": "Memory is on 🌿, but there is nothing useful I need to carry forward yet. Once a name, city, or open topic matters, I can use it so we do not start from zero again.",
            "uk": "Пам’ять увімкнена 🌿, але поки немає корисного контексту, який треба перенести далі. Коли з’явиться ім’я, місто чи відкрита справа, я зможу продовжити без початку з нуля.",
            "el": "Η μνήμη είναι ενεργή 🌿, αλλά ακόμη δεν υπάρχει χρήσιμο πλαίσιο που χρειάζεται να κρατήσω για συνέχεια. Όταν υπάρξει όνομα, πόλη ή ανοιχτό θέμα, θα μπορούμε να συνεχίζουμε χωρίς να ξεκινάμε από το μηδέν.",
        }[lang]

    first_name = str(profile.get("first_name") or "").strip()
    current_topic = str(profile.get("current_topic") or "").strip()
    intro = {
        "ar": f"إي{f' يا {first_name}' if first_name else ''}، متذكّر عنك هالأشياء اللي بتفيدنا حتى ما نرجع من الصفر كل مرة 🌿:",
        "de": f"Ja{f', {first_name}' if first_name else ''} — diese Dinge merke ich mir, damit wir nicht jedes Mal von vorn anfangen 🌿:",
        "en": f"Yes{f', {first_name}' if first_name else ''} — these are the useful details I remember so we do not have to start from zero each time 🌿:",
        "uk": f"Так{f', {first_name}' if first_name else ''} — ось корисні речі, які я пам’ятаю, щоб нам не починати щоразу з нуля 🌿:",
        "el": f"Ναι{f', {first_name}' if first_name else ''} — αυτά είναι τα χρήσιμα στοιχεία που θυμάμαι ώστε να μη ξεκινάμε κάθε φορά από το μηδέν 🌿:",
    }[lang]
    continuation = {
        "ar": f"إذا رجعنا لـ«{current_topic}»، ما تعيدلي القصة من أولها؛ قلّي «نكمل» ومنمشي من محل ما وقفنا." if current_topic else "ولما يصير في موضوع عم نتابعه، بخليه مربوط بالسياق حتى ما تضطر تعيد نفس الشرح.",
        "de": f"Wenn wir zu „{current_topic}“ zurückkehren, musst du nicht alles neu erzählen — schreib einfach „weiter“, und wir knüpfen dort an." if current_topic else "Sobald wir ein Thema gemeinsam verfolgen, halte ich den nützlichen Kontext zusammen, damit du dich nicht wiederholen musst.",
        "en": f"If we return to “{current_topic}”, you do not need to tell the whole story again — just say “continue” and we will pick it up from there." if current_topic else "Once we are following a topic together, I keep the useful context connected so you do not have to repeat yourself.",
        "uk": f"Якщо повернемося до «{current_topic}», не потрібно розповідати все заново — напиши «продовжуємо», і підхопимо з того місця." if current_topic else "Коли ми ведемо справу разом, я тримаю корисний контекст пов’язаним, щоб тобі не доводилося повторюватися.",
        "el": f"Αν επιστρέψουμε στο «{current_topic}», δεν χρειάζεται να τα πεις όλα από την αρχή — γράψε «συνεχίζουμε» και πιάνουμε το νήμα από εκεί." if current_topic else "Όταν παρακολουθούμε ένα θέμα μαζί, κρατώ συνδεδεμένο το χρήσιμο πλαίσιο ώστε να μη χρειάζεται να επαναλαμβάνεσαι.",
    }[lang]
    return intro + "\n• " + "\n• ".join(facts) + "\n\n" + continuation
