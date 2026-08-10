"""Privacy-safe user controls for Closed Beta participation metadata.

The helpers in this module never log or return recipient identifiers. They only
recognize explicit user commands and render identifier-free user-facing status.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

_DELETE_PATTERNS = (
    "lösch meine daten",
    "daten löschen",
    "delete my data",
    "امسح بياناتي",
    "احذف بياناتي",
    "видали мої дані",
    "διαγραψε τα δεδομενα μου",
    "διαγραφή δεδομένων μου",
)
_LEAVE_PATTERNS = {
    "اخرج من النسخة التجريبية",
    "الغ مشاركتي بالنسخة التجريبية",
    "ألغي مشاركتي بالنسخة التجريبية",
    "ما بدي كمل بالبيتا",
    "closed beta verlassen",
    "beta verlassen",
    "teilnahme an der beta beenden",
    "leave closed beta",
    "leave beta",
    "end beta participation",
    "вийти з beta",
    "припинити участь у beta",
    "αποχωρηση απο beta",
    "αποχώρηση από beta",
    "τερματισμος συμμετοχης beta",
    "τερματισμός συμμετοχής beta",
}


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text or "").casefold().strip()
    value = re.sub(r"[\u064b-\u065f\u0670\u0640]", "", value)
    value = value.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا"}))
    value = re.sub(r"[؟،؛!?.,:;]+", " ", value)
    value = re.sub(r"[^\w\u0600-\u06ff\u0370-\u03ff\u0400-\u04ff]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


_NORMALIZED_LEAVE_PATTERNS = frozenset(_normalize(item) for item in _LEAVE_PATTERNS)
_NORMALIZED_DELETE_PATTERNS = tuple(_normalize(item) for item in _DELETE_PATTERNS)


def is_delete_request(text: str) -> bool:
    normalized = _normalize(text)
    return any(phrase in normalized for phrase in _NORMALIZED_DELETE_PATTERNS)


def is_leave_request(text: str) -> bool:
    return _normalize(text) in _NORMALIZED_LEAVE_PATTERNS


def beta_left_message(language: str) -> str:
    return {
        "ar": "تم إنهاء مشاركتك بالنسخة التجريبية وتحرير مكانك. بياناتك الأخرى لم تُحذف.",
        "de": "Deine Teilnahme an der Closed Beta wurde beendet und dein Platz freigegeben. Andere Daten wurden nicht gelöscht.",
        "en": "Your Closed Beta participation has ended and your slot was released. Your other data was not deleted.",
        "uk": "Вашу участь у закритій Beta завершено, а місце звільнено. Інші дані не видалялися.",
        "el": "Η συμμετοχή σας στην κλειστή Beta τερματίστηκε και η θέση ελευθερώθηκε. Τα υπόλοιπα δεδομένα δεν διαγράφηκαν.",
    }.get(language, "Deine Teilnahme an der Closed Beta wurde beendet und dein Platz freigegeben. Andere Daten wurden nicht gelöscht.")


def beta_not_active_message(language: str) -> str:
    return {
        "ar": "ما عندك مشاركة فعّالة بالنسخة التجريبية حاليًا.",
        "de": "Du hast derzeit keine aktive Closed-Beta-Teilnahme.",
        "en": "You do not currently have an active Closed Beta participation.",
        "uk": "Наразі у вас немає активної участі в закритій Beta.",
        "el": "Δεν έχετε αυτή τη στιγμή ενεργή συμμετοχή στην κλειστή Beta.",
    }.get(language, "Du hast derzeit keine aktive Closed-Beta-Teilnahme.")


def beta_privacy_unavailable_message(language: str) -> str:
    return {
        "ar": "تعذر إكمال العملية على بيانات مشاركتك بالنسخة التجريبية بشكل موثوق. لا أستطيع تأكيد نتيجة كاملة الآن؛ جرّب مرة ثانية لاحقًا.",
        "de": "Der Vorgang für deine Closed-Beta-Daten konnte nicht zuverlässig abgeschlossen werden. Ein vollständiges Ergebnis kann gerade nicht bestätigt werden; bitte versuche es später erneut.",
        "en": "The operation on your Closed Beta data could not be completed reliably. A complete result cannot be confirmed right now; please try again later.",
        "uk": "Операцію з даними участі в закритій Beta не вдалося надійно завершити. Повний результат зараз неможливо підтвердити; спробуйте пізніше.",
        "el": "Η ενέργεια στα δεδομένα συμμετοχής στην κλειστή Beta δεν ολοκληρώθηκε αξιόπιστα. Δεν μπορεί να επιβεβαιωθεί πλήρες αποτέλεσμα τώρα· δοκιμάστε αργότερα.",
    }.get(language, "Der Vorgang für deine Closed-Beta-Daten konnte nicht zuverlässig abgeschlossen werden. Ein vollständiges Ergebnis kann gerade nicht bestätigt werden; bitte versuche es später erneut.")


def render_beta_export(language: str, payload: dict[str, Any]) -> str:
    """Render only identifier-free participation metadata for a user export."""
    if payload.get("status") != "available":
        return {
            "ar": "بيانات المشاركة بالنسخة التجريبية: غير متاحة مؤقتًا للتحقق.",
            "de": "Closed-Beta-Teilnahmedaten: vorübergehend nicht verifizierbar.",
            "en": "Closed Beta participation data: temporarily unavailable for verification.",
            "uk": "Дані участі в закритій Beta: тимчасово недоступні для перевірки.",
            "el": "Δεδομένα συμμετοχής στην κλειστή Beta: προσωρινά μη διαθέσιμα για επαλήθευση.",
        }.get(language, "Closed-Beta-Teilnahmedaten: vorübergehend nicht verifizierbar.")

    records = payload.get("records", []) if isinstance(payload.get("records"), list) else []
    if not records:
        return {
            "ar": "بيانات المشاركة بالنسخة التجريبية: لا توجد مشاركة محفوظة.",
            "de": "Closed-Beta-Teilnahmedaten: keine Teilnahme gespeichert.",
            "en": "Closed Beta participation data: no participation is stored.",
            "uk": "Дані участі в закритій Beta: участь не збережена.",
            "el": "Δεδομένα συμμετοχής στην κλειστή Beta: δεν υπάρχει αποθηκευμένη συμμετοχή.",
        }.get(language, "Closed-Beta-Teilnahmedaten: keine Teilnahme gespeichert.")

    headings = {
        "ar": "بيانات المشاركة بالنسخة التجريبية:",
        "de": "Closed-Beta-Teilnahmedaten:",
        "en": "Closed Beta participation data:",
        "uk": "Дані участі в закритій Beta:",
        "el": "Δεδομένα συμμετοχής στην κλειστή Beta:",
    }
    labels = {
        "ar": ("الموجة", "الحالة", "نسخة الموافقة", "تاريخ القبول", "تاريخ الإنهاء"),
        "de": ("Welle", "Status", "Einwilligungsversion", "Aufnahme", "Beendigung"),
        "en": ("wave", "status", "consent version", "admitted at", "ended at"),
        "uk": ("хвиля", "статус", "версія згоди", "дата приєднання", "дата завершення"),
        "el": ("κύμα", "κατάσταση", "έκδοση συγκατάθεσης", "ένταξη", "λήξη"),
    }
    safe_language = language if language in headings else "de"
    wave_label, status_label, consent_label, admitted_label, revoked_label = labels[safe_language]
    lines = [headings[safe_language]]
    for record in records[:20]:
        if not isinstance(record, dict):
            continue
        fields = [
            f"{wave_label}={str(record.get('wave') or '')[:40]}",
            f"{status_label}={str(record.get('status') or '')[:20]}",
            f"{consent_label}={str(record.get('consent_version') or '')[:80]}",
            f"{admitted_label}={str(record.get('admitted_at') or '')[:40]}",
        ]
        if record.get("revoked_at"):
            fields.append(f"{revoked_label}={str(record.get('revoked_at'))[:40]}")
        lines.append("- " + "; ".join(fields))
    return "\n".join(lines)
