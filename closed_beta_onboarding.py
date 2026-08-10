"""Closed Beta onboarding policy and user-facing opt-in boundary.

This module is side-effect free. It parses configuration, renders the approved Beta
notice, and interprets opt-in decisions. It does not persist admission, send
messages, or enable production admission by itself.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Mapping

from closed_beta_admission import AdmissionPolicy

BETA_NOTICE_VERSION = "2026-08-wave1-v1"
_SUPPORTED_LANGUAGES = frozenset({"de", "ar", "en", "uk", "el"})
_SCOPE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class ClosedBetaOnboardingConfig:
    enabled: bool = False
    capacity: int = 5
    wave: str = "wave1"
    tenant_key: str = "default"
    notice_version: str = BETA_NOTICE_VERSION

    @property
    def policy(self) -> AdmissionPolicy:
        return AdmissionPolicy(enabled=self.enabled, capacity=self.capacity)


def _strict_flag(env: Mapping[str, str], name: str, *, default: bool = False) -> bool:
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        return default
    value = str(raw).strip().casefold()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ValueError(f"{name.lower()}_invalid")


def onboarding_config(env: Mapping[str, str]) -> ClosedBetaOnboardingConfig:
    enabled = _strict_flag(env, "CLOSED_BETA_ADMISSION_ENABLED", default=False)
    raw_capacity = str(env.get("CLOSED_BETA_ADMISSION_CAPACITY", "5")).strip()
    try:
        capacity = int(raw_capacity)
    except ValueError as exc:
        raise ValueError("closed_beta_admission_capacity_invalid") from exc
    if not 1 <= capacity <= 100:
        raise ValueError("closed_beta_admission_capacity_invalid")

    wave = str(env.get("CLOSED_BETA_ADMISSION_WAVE", "wave1")).strip().casefold()
    tenant_key = str(env.get("CLOSED_BETA_TENANT_KEY", "default")).strip().casefold()
    if not _SCOPE_PATTERN.fullmatch(wave):
        raise ValueError("closed_beta_admission_wave_invalid")
    if not _SCOPE_PATTERN.fullmatch(tenant_key):
        raise ValueError("closed_beta_tenant_key_invalid")

    notice_version = str(env.get("CLOSED_BETA_NOTICE_VERSION", BETA_NOTICE_VERSION)).strip()
    if notice_version != BETA_NOTICE_VERSION:
        raise ValueError("closed_beta_notice_version_invalid")

    return ClosedBetaOnboardingConfig(
        enabled=enabled,
        capacity=capacity,
        wave=wave,
        tenant_key=tenant_key,
        notice_version=notice_version,
    )


def _lang(language: str) -> str:
    return language if language in _SUPPORTED_LANGUAGES else "de"


def beta_notice(language: str) -> str:
    """Return the approved concise Closed Beta notice without memory consent."""
    messages = {
        "de": (
            "AmtHero24 befindet sich in einer kleinen geschlossenen Beta. Sam hilft dir bei "
            "Alltag und Verwaltung in Deutschland, kann aber Fehler machen und ersetzt keine "
            "Behörde, Rechtsberatung oder medizinische Beratung. Bitte prüfe wichtige Angaben, "
            "Fristen und Entwürfe vor einer Handlung. Dokumente und Audio werden nur zur "
            "Verarbeitung genutzt und nicht als Rohdateien dauerhaft gespeichert. Die Teilnahme "
            "ist freiwillig und kann jederzeit beendet werden. Möchtest du an dieser Beta teilnehmen?"
        ),
        "ar": (
            "AmtHero24 حاليًا ضمن نسخة تجريبية مغلقة وصغيرة. Sam يساعدك في أمور الحياة اليومية "
            "والمعاملات في ألمانيا، لكنه قد يخطئ ولا يستبدل جهة رسمية أو استشارة قانونية أو طبية. "
            "راجع المعلومات المهمة والمواعيد والنصوص قبل اتخاذ أي إجراء. المستندات والصوت تُستخدم "
            "للمعالجة فقط ولا تُحفظ كملفات خام بشكل دائم. المشاركة اختيارية ويمكنك إيقافها بأي وقت. "
            "هل ترغب بالمشاركة في هذه النسخة التجريبية؟"
        ),
        "en": (
            "AmtHero24 is currently in a small Closed Beta. Sam can help with everyday administrative "
            "tasks in Germany, but may make mistakes and does not replace an authority, legal advice, "
            "or medical advice. Review important facts, deadlines, and drafts before acting. Documents "
            "and audio are used for processing only and are not retained as raw files permanently. "
            "Participation is voluntary and can be ended at any time. Would you like to join this Beta?"
        ),
        "uk": (
            "AmtHero24 зараз працює як невелика закрита бета-версія. Sam допомагає з повсякденними "
            "адміністративними справами в Німеччині, але може помилятися й не замінює державний орган, "
            "юридичну чи медичну консультацію. Перевіряйте важливі дані, строки та тексти перед дією. "
            "Документи й аудіо використовуються лише для обробки та не зберігаються постійно як сирі "
            "файли. Участь добровільна й її можна припинити будь-коли. Бажаєте приєднатися до Beta?"
        ),
        "el": (
            "Το AmtHero24 βρίσκεται τώρα σε μικρή κλειστή δοκιμαστική έκδοση. Ο Sam βοηθά σε "
            "καθημερινές διοικητικές υποθέσεις στη Γερμανία, αλλά μπορεί να κάνει λάθη και δεν "
            "αντικαθιστά δημόσια αρχή, νομική ή ιατρική συμβουλή. Ελέγχετε σημαντικά στοιχεία, "
            "προθεσμίες και κείμενα πριν ενεργήσετε. Έγγραφα και ήχος χρησιμοποιούνται μόνο για "
            "επεξεργασία και δεν διατηρούνται μόνιμα ως ακατέργαστα αρχεία. Η συμμετοχή είναι "
            "εθελοντική και μπορεί να τερματιστεί οποτεδήποτε. Θέλετε να συμμετάσχετε στη Beta;"
        ),
    }
    return messages[_lang(language)]


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").casefold().strip()
    value = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", value)
    value = re.sub(r"[؟،؛!?.,:;]+", " ", value)
    value = re.sub(r"[^\w\u0600-\u06ff\u0370-\u03ff\u0400-\u04ff]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


_BETA_YES = {
    "نعم", "اي", "إي", "موافق", "ارغب بالمشاركة", "بدي شارك",
    "ja", "ich möchte teilnehmen", "beta beitreten",
    "yes", "i want to join", "join beta",
    "так", "хочу приєднатися", "приєднатися до beta",
    "ναι", "θελω να συμμετασχω", "συμμετοχη beta",
}
_BETA_NO = {
    "لا", "لأ", "لا اريد", "ما بدي شارك",
    "nein", "ich möchte nicht teilnehmen", "nicht teilnehmen",
    "no", "i do not want to join", "do not join",
    "ні", "не хочу приєднуватися",
    "όχι", "δεν θελω να συμμετασχω",
}
_NORMALIZED_YES = frozenset(_normalize(value) for value in _BETA_YES)
_NORMALIZED_NO = frozenset(_normalize(value) for value in _BETA_NO)


def beta_opt_in_decision(text: str) -> bool | None:
    normalized = _normalize(text)
    if normalized in _NORMALIZED_YES:
        return True
    if normalized in _NORMALIZED_NO:
        return False
    return None


def beta_declined_message(language: str) -> str:
    return {
        "de": "Kein Problem. Du wurdest nicht zur Closed Beta zugelassen.",
        "ar": "تمام، ما في مشكلة. ما تم إدخالك إلى النسخة التجريبية المغلقة.",
        "en": "No problem. You have not been admitted to the Closed Beta.",
        "uk": "Без проблем. Вас не було додано до закритої Beta.",
        "el": "Κανένα πρόβλημα. Δεν έχετε ενταχθεί στην κλειστή Beta.",
    }[_lang(language)]


def beta_full_message(language: str) -> str:
    return {
        "de": "Die aktuelle Closed-Beta-Gruppe ist voll. Es wurde kein Platz für dich belegt.",
        "ar": "المجموعة الحالية للنسخة التجريبية ممتلئة. ما تم حجز مكان إلك.",
        "en": "The current Closed Beta group is full. No slot was consumed for you.",
        "uk": "Поточна група закритої Beta заповнена. Місце для вас не було зайнято.",
        "el": "Η τρέχουσα ομάδα της κλειστής Beta είναι πλήρης. Δεν δεσμεύτηκε θέση για εσάς.",
    }[_lang(language)]


def beta_admitted_message(language: str) -> str:
    return {
        "de": "Du bist jetzt für die Closed Beta zugelassen. Als Nächstes richten wir Sam für dich ein.",
        "ar": "تم قبولك بالنسخة التجريبية المغلقة. الخطوة الجاية منجهز Sam إلك.",
        "en": "You are now admitted to the Closed Beta. Next, we will set up Sam for you.",
        "uk": "Вас додано до закритої Beta. Далі ми налаштуємо Sam для вас.",
        "el": "Έχετε πλέον ενταχθεί στην κλειστή Beta. Στη συνέχεια θα ρυθμίσουμε τον Sam για εσάς.",
    }[_lang(language)]
