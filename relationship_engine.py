"""Consent-aware conversation adaptation for Sam.

This module keeps long-term preferences explicit and low-risk. Transient mood
signals are used only to shape the current conversation and are never persisted.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

SUPPORTED_LANGUAGES = {"de", "ar", "en", "uk", "el"}

_LANGUAGE_LABELS = {
    "ar": {"ar": "العربية", "de": "الألمانية", "en": "الإنجليزية", "uk": "الأوكرانية", "el": "اليونانية"},
    "de": {"ar": "Arabisch", "de": "Deutsch", "en": "Englisch", "uk": "Ukrainisch", "el": "Griechisch"},
    "en": {"ar": "Arabic", "de": "German", "en": "English", "uk": "Ukrainian", "el": "Greek"},
    "uk": {"ar": "арабська", "de": "німецька", "en": "англійська", "uk": "українська", "el": "грецька"},
    "el": {"ar": "Αραβικά", "de": "Γερμανικά", "en": "Αγγλικά", "uk": "Ουκρανικά", "el": "Ελληνικά"},
}

_TOPIC_LABELS = {
    "ar": {
        "languages": "اللغات التي يدعمها AmtHero24", "capabilities": "ميزات AmtHero24",
        "invoice": "فاتورة أو دفعة", "document": "مستند أو رسالة", "housing": "السكن والإيجار",
        "work": "العمل والراتب", "residence": "الإقامة والفيزا", "benefits": "المساعدات والدوائر",
        "health": "التأمين والصحة", "unknown": "موضوع غير محدد",
    },
    "de": {
        "languages": "unterstützte Sprachen", "capabilities": "Funktionen von AmtHero24",
        "invoice": "Rechnung oder Zahlung", "document": "Dokument oder Schreiben", "housing": "Wohnen und Miete",
        "work": "Arbeit und Gehalt", "residence": "Aufenthalt und Visum", "benefits": "Leistungen und Behörden",
        "health": "Versicherung und Gesundheit", "unknown": "kein festes Thema",
    },
    "en": {
        "languages": "supported languages", "capabilities": "AmtHero24 features",
        "invoice": "invoice or payment", "document": "document or letter", "housing": "housing and rent",
        "work": "work and salary", "residence": "residence and visa", "benefits": "benefits and authorities",
        "health": "insurance and health", "unknown": "no fixed topic",
    },
    "uk": {
        "languages": "підтримувані мови", "capabilities": "можливості AmtHero24",
        "invoice": "рахунок або платіж", "document": "документ або лист", "housing": "житло та оренда",
        "work": "робота та зарплата", "residence": "проживання та віза", "benefits": "виплати та установи",
        "health": "страхування та здоров’я", "unknown": "тема не визначена",
    },
    "el": {
        "languages": "υποστηριζόμενες γλώσσες", "capabilities": "δυνατότητες AmtHero24",
        "invoice": "τιμολόγιο ή πληρωμή", "document": "έγγραφο ή επιστολή", "housing": "στέγαση και ενοίκιο",
        "work": "εργασία και μισθός", "residence": "διαμονή και βίζα", "benefits": "παροχές και υπηρεσίες",
        "health": "ασφάλιση και υγεία", "unknown": "χωρίς συγκεκριμένο θέμα",
    },
}

_STYLE_VALUE_LABELS = {
    "ar": {
        "concise": "مختصر ومباشر", "detailed": "مفصل خطوة بخطوة", "balanced": "متوازن",
        "formal": "رسمي", "friendly": "ودّي وبسيط", "levantine": "شامي/سوري",
        "egyptian": "مصري", "iraqi": "عراقي", "minimal": "استخدام الاسم باعتدال",
    },
    "de": {
        "concise": "kurz und direkt", "detailed": "detailliert und Schritt für Schritt", "balanced": "ausgewogen",
        "formal": "formell", "friendly": "freundlich und einfach", "levantine": "levantinisches Arabisch",
        "egyptian": "ägyptisches Arabisch", "iraqi": "irakisches Arabisch", "minimal": "Name sparsam verwenden",
    },
    "en": {
        "concise": "short and direct", "detailed": "detailed and step by step", "balanced": "balanced",
        "formal": "formal", "friendly": "friendly and simple", "levantine": "Levantine Arabic",
        "egyptian": "Egyptian Arabic", "iraqi": "Iraqi Arabic", "minimal": "use my name sparingly",
    },
    "uk": {
        "concise": "коротко й прямо", "detailed": "детально, крок за кроком", "balanced": "збалансовано",
        "formal": "офіційно", "friendly": "дружньо й просто", "levantine": "левантійська арабська",
        "egyptian": "єгипетська арабська", "iraqi": "іракська арабська", "minimal": "рідко використовувати ім’я",
    },
    "el": {
        "concise": "σύντομα και άμεσα", "detailed": "αναλυτικά, βήμα βήμα", "balanced": "ισορροπημένα",
        "formal": "επίσημα", "friendly": "φιλικά και απλά", "levantine": "λεβαντίνικα αραβικά",
        "egyptian": "αιγυπτιακά αραβικά", "iraqi": "ιρακινά αραβικά", "minimal": "σπάνια χρήση ονόματος",
    },
}

_LONG_TERM_MARKERS = (
    "من هلق", "من الآن", "دايما", "دائما", "عادة", "بحب", "بفضل", "خليك", "خلّيك",
    "ab jetzt", "immer", "normalerweise", "ich bevorzuge", "bitte künftig",
    "from now on", "always", "usually", "i prefer",
    "відтепер", "завжди", "я віддаю перевагу", "απο εδω και περα", "παντα", "προτιμω",
)


@dataclass(frozen=True)
class PreferenceResult:
    settings: dict[str, str]
    changed: dict[str, str]
    persistent: bool
    command_only: bool


def _lang(language: str) -> str:
    return language if language in SUPPORTED_LANGUAGES else "de"


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").casefold().strip()
    value = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", value)
    value = re.sub(r"[؟،؛!?.,:;]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_style(value: Any) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for part in str(value or "").split(";"):
        if "=" not in part:
            continue
        key, raw = part.split("=", 1)
        key = key.strip()
        raw = raw.strip()
        if key in {"detail", "tone", "dialect", "name"} and raw:
            parsed[key] = raw[:24]
    return parsed


def serialize_style(settings: dict[str, str]) -> str:
    order = ("detail", "tone", "dialect", "name")
    return ";".join(f"{key}={settings[key]}" for key in order if settings.get(key))[:80]


def _contains_any(normalized: str, phrases: Iterable[str]) -> bool:
    return any(_normalize(phrase) in normalized for phrase in phrases)


def analyze_preferences(text: str, existing: Any = "") -> PreferenceResult:
    normalized = _normalize(text)
    settings = parse_style(existing)
    changed: dict[str, str] = {}

    detail_concise = (
        "جاوبني باختصار", "اختصر جوابك", "خليك مختصر", "رد مختصر", "kurz antworten",
        "bitte kurz", "keep it short", "be concise", "відповідай коротко", "συντομα",
    )
    detail_detailed = (
        "اشرحلي بالتفصيل", "اشرح بالتفصيل", "فصللي خطوة خطوة", "بدي شرح مفصل",
        "ausführlich erklären", "detailliert erklären", "explain in detail", "step by step",
        "пояснюй детально", "αναλυτικα",
    )
    tone_formal = ("احكي معي رسمي", "خليك رسمي", "بأسلوب رسمي", "formell antworten", "be formal", "офіційно", "επισημα")
    tone_friendly = (
        "احكي معي عادي", "احكي معي ببساطة", "خليك ودي", "خليك طبيعي", "freundlich und einfach",
        "talk naturally", "keep it friendly", "дружньо", "φιλικα",
    )
    name_minimal = (
        "لا تكرر اسمي", "لا تناديني باسمي كل مرة", "استخدم اسمي قليل", "meinen namen nicht ständig",
        "do not repeat my name", "use my name less", "не повторюй моє ім я", "μην επαναλαμβανεις το ονομα μου",
    )

    if _contains_any(normalized, detail_concise):
        changed["detail"] = "concise"
    elif _contains_any(normalized, detail_detailed):
        changed["detail"] = "detailed"

    if _contains_any(normalized, tone_formal):
        changed["tone"] = "formal"
    elif _contains_any(normalized, tone_friendly):
        changed["tone"] = "friendly"

    if _contains_any(normalized, name_minimal):
        changed["name"] = "minimal"

    dialect_patterns = {
        "levantine": ("عالسوري", "بالسوري", "بالشامي", "لهجة شامية", "لهجة سورية"),
        "egyptian": ("بالمصري", "لهجة مصرية"),
        "iraqi": ("بالعراقي", "لهجة عراقية"),
    }
    for dialect, patterns in dialect_patterns.items():
        if _contains_any(normalized, patterns):
            changed["dialect"] = dialect
            changed.setdefault("tone", "friendly")
            break

    settings.update(changed)
    persistent = bool(changed) and (
        _contains_any(normalized, _LONG_TERM_MARKERS)
        or "dialect" in changed
        or "name" in changed
    )
    word_count = len(normalized.split())
    command_only = bool(changed) and word_count <= 10 and not any(
        term in normalized
        for term in ("فاتورة", "رسالة", "ايميل", "إيميل", "brief", "rechnung", "email", "invoice", "document")
    )
    return PreferenceResult(settings=settings, changed=changed, persistent=persistent, command_only=command_only)


def detect_mood_signal(text: str, history: list[str] | None = None) -> str:
    combined = " ".join([*(history or [])[-3:], text or ""])
    normalized = _normalize(combined)
    signals = {
        "urgent": ("مستعجل", "بسرعة", "ضروري اليوم", "dringend", "sofort", "urgent", "терміново", "επειγον"),
        "stressed": ("متوتر", "خايف", "قلقان", "مضغوط", "angst", "gestresst", "worried", "stressed", "хвилююсь", "αγχωμενος"),
        "confused": ("ما فهمت", "مو فاهم", "ضايع", "محتار", "verstehe nicht", "verwirrt", "confused", "не розумію", "δεν καταλαβαινω"),
        "positive": ("ممتاز", "تمام جدا", "شكرا كتير", "super", "perfekt", "thank you", "great", "дякую", "τελεια"),
    }
    for signal, phrases in signals.items():
        if _contains_any(normalized, phrases):
            return signal
    return "neutral"


def preference_ack(language: str, result: PreferenceResult, *, persisted: bool) -> str:
    lang = _lang(language)
    labels = _STYLE_VALUE_LABELS[lang]
    values = [labels.get(value, value) for value in result.changed.values()]
    joined = "، ".join(values) if lang == "ar" else ", ".join(values)
    if lang == "ar":
        suffix = "ورح أتذكّر هالاختيار للمرة الجاية ✅" if persisted else "ورح أمشي عليه بهالمحادثة ✅"
        return f"تمام، صار أسلوبي معك: {joined}. {suffix}"
    if lang == "de":
        suffix = "Ich merke mir das fürs nächste Mal ✅" if persisted else "Ich halte mich in diesem Gespräch daran ✅"
        return f"Alles klar. Mein Stil für dich: {joined}. {suffix}"
    if lang == "en":
        suffix = "I’ll remember that for next time ✅" if persisted else "I’ll follow it in this conversation ✅"
        return f"Got it. Your preferred style is: {joined}. {suffix}"
    if lang == "uk":
        suffix = "Я запам’ятаю це на майбутнє ✅" if persisted else "Дотримуватимусь цього в цій розмові ✅"
        return f"Домовились. Бажаний стиль: {joined}. {suffix}"
    suffix = "Θα το θυμάμαι και την επόμενη φορά ✅" if persisted else "Θα το ακολουθήσω σε αυτή τη συζήτηση ✅"
    return f"Έγινε. Προτιμώμενο ύφος: {joined}. {suffix}"


def augment_prompt(base_prompt: str, *, profile: dict[str, Any], text: str, history: list[str]) -> str:
    persisted = parse_style(profile.get("communication_style"))
    session_settings = dict(persisted)
    for item in [*history[-5:], text]:
        result = analyze_preferences(item, serialize_style(session_settings))
        session_settings = result.settings
    mood = detect_mood_signal(text, history)

    detail = session_settings.get("detail", "balanced")
    tone = session_settings.get("tone", "friendly")
    dialect = session_settings.get("dialect", "")
    name_usage = session_settings.get("name", "normal")

    guidance = [
        "RELATIONSHIP ADAPTATION",
        f"- Current conversational signal: {mood}. Treat it as temporary context, not a permanent personality label.",
        f"- Preferred response detail: {detail}.",
        f"- Preferred tone: {tone}.",
        f"- Arabic dialect preference: {dialect or 'none explicitly requested'}.",
        f"- Name usage preference: {name_usage}.",
        "- If the signal is urgent, lead with the single next action and keep the answer short.",
        "- If the signal is stressed, acknowledge it in one calm sentence, then give practical steps; avoid humor.",
        "- If the signal is confused, explain one step at a time and check understanding without sounding robotic.",
        "- If detail is concise, use no more than 5 short lines unless safety requires more.",
        "- If detail is detailed, use a clear sequence but avoid unnecessary repetition.",
        "- If tone is formal, stay respectful and restrained. If friendly, sound natural and warm.",
        "- Use an explicitly requested Arabic dialect naturally, but never infer ethnicity, nationality, or origin from it.",
        "- Do not manufacture intimacy, dependency, guilt, urgency, or sales pressure. Earn trust through useful continuity.",
    ]
    return base_prompt + "\n\n" + "\n".join(guidance)


def _human_language(output_language: str, value: str) -> str:
    return _LANGUAGE_LABELS[_lang(output_language)].get(value, value)


def _human_topic(output_language: str, value: str) -> str:
    return _TOPIC_LABELS[_lang(output_language)].get(value, value.replace("_", " "))


def _style_summary(output_language: str, value: Any) -> str:
    lang = _lang(output_language)
    settings = parse_style(value)
    labels = _STYLE_VALUE_LABELS[lang]
    return ", ".join(labels.get(raw, raw) for raw in settings.values())


def human_memory_summary(language: str, profile: dict[str, Any]) -> str:
    lang = _lang(language)
    if profile.get("memory_consent") != "granted":
        return {
            "ar": "حاليًا ما عندي معلومات شخصية محفوظة عنك. إذا بتحب، منبلّش باسمك وبسألك بوضوح قبل ما أحفظ أي شي للمرة الجاية.",
            "de": "Aktuell habe ich keine persönlichen Angaben gespeichert. Wenn du möchtest, starten wir mit deinem Vornamen; vor dem Speichern frage ich ausdrücklich nach deiner Zustimmung.",
            "en": "I do not currently have personal information saved. We can start with your first name, and I’ll ask clearly before saving anything for next time.",
            "uk": "Зараз персональні дані не збережені. Можемо почати з імені, а перед збереженням я чітко попрошу згоду.",
            "el": "Δεν έχω αποθηκευμένες προσωπικές πληροφορίες. Μπορούμε να ξεκινήσουμε με το όνομά σου και θα ζητήσω καθαρά άδεια πριν αποθηκεύσω οτιδήποτε.",
        }[lang]

    facts: list[str] = []
    labels = {
        "ar": {"name": "اسمك", "language": "لغتك", "city": "مدينتك", "topic": "الموضوع الحالي", "style": "أسلوبك المفضل"},
        "de": {"name": "Name", "language": "Sprache", "city": "Stadt", "topic": "Aktuelles Thema", "style": "Bevorzugter Stil"},
        "en": {"name": "Name", "language": "Language", "city": "City", "topic": "Current topic", "style": "Preferred style"},
        "uk": {"name": "Ім’я", "language": "Мова", "city": "Місто", "topic": "Поточна тема", "style": "Бажаний стиль"},
        "el": {"name": "Όνομα", "language": "Γλώσσα", "city": "Πόλη", "topic": "Τρέχον θέμα", "style": "Προτιμώμενο ύφος"},
    }[lang]
    if profile.get("first_name"):
        facts.append(f"{labels['name']}: {profile['first_name']}")
    if profile.get("preferred_language"):
        facts.append(f"{labels['language']}: {_human_language(lang, str(profile['preferred_language']))}")
    if profile.get("city"):
        facts.append(f"{labels['city']}: {profile['city']}")
    topic = str(profile.get("current_topic") or "").strip()
    if topic and topic != "unknown":
        facts.append(f"{labels['topic']}: {_human_topic(lang, topic)}")
    style = _style_summary(lang, profile.get("communication_style"))
    if style:
        facts.append(f"{labels['style']}: {style}")

    if not facts:
        return {
            "ar": "الذاكرة مفعّلة، بس ما عندي معلومات مفيدة محفوظة عنك لسا.",
            "de": "Die Erinnerung ist aktiviert, aber bisher sind keine hilfreichen Angaben gespeichert.",
            "en": "Memory is enabled, but no useful details have been saved yet.",
            "uk": "Пам’ять увімкнена, але корисних даних поки немає.",
            "el": "Η μνήμη είναι ενεργή, αλλά δεν υπάρχουν ακόμη χρήσιμες πληροφορίες.",
        }[lang]
    intro = {
        "ar": "هاي المعلومات المفيدة اللي متذكّرها عنك:",
        "de": "Das sind die hilfreichen Angaben, die ich mir gemerkt habe:",
        "en": "Here are the useful details I remember:",
        "uk": "Ось корисні дані, які я пам’ятаю:",
        "el": "Αυτές είναι οι χρήσιμες πληροφορίες που θυμάμαι:",
    }[lang]
    return intro + "\n• " + "\n• ".join(facts)


def human_export_reply(language: str, payload: dict[str, Any]) -> str:
    lang = _lang(language)
    profile = payload.get("profile", {}) if isinstance(payload.get("profile"), dict) else {}
    missions = payload.get("missions", []) if isinstance(payload.get("missions"), list) else []
    profile_text = human_memory_summary(lang, {**profile, "memory_consent": "granted"}) if profile else ""
    mission_lines = [
        f"• {item.get('title')} — {item.get('status')}"
        for item in missions
        if isinstance(item, dict) and item.get("title")
    ]
    if not profile_text and not mission_lines:
        return {
            "ar": "ما عندي بيانات شخصية محفوظة عنك حاليًا.", "de": "Derzeit sind keine persönlichen Daten gespeichert.",
            "en": "I currently have no personal data saved.", "uk": "Зараз персональні дані не збережені.",
            "el": "Δεν υπάρχουν αποθηκευμένα προσωπικά δεδομένα.",
        }[lang]
    mission_heading = {
        "ar": "\n\nمهامك:", "de": "\n\nDeine Aufgaben:", "en": "\n\nYour tasks:",
        "uk": "\n\nТвої завдання:", "el": "\n\nΟι εργασίες σου:",
    }[lang]
    return profile_text + (mission_heading + "\n" + "\n".join(mission_lines) if mission_lines else "")
