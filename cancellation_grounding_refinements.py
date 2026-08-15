"""Small compatibility refinements for cancellation grounding output.

The core grounder owns validation and safety. This module keeps the production-facing
wording backward compatible with the shared official-draft contract and improves
explicit contract-start presentation without replacing or mutating core validators.
"""
from __future__ import annotations

import re

import cancellation_draft_grounding as _base


def _contract_start_date(draft: str) -> str:
    """Return an explicitly labelled contract start date, never an arbitrary date."""
    text = _base._normalize(draft)  # noqa: SLF001 - same-package read-only helper
    patterns = (
        re.compile(
            r"(?:Vertragsbeginn|Vertrag\s+begann(?:en)?|Der\s+Vertrag\s+begann|"
            r"contract\s+start(?:ed)?|contract\s+began|تاريخ\s+بدء\s+العقد|بدأ\s+العقد|"
            r"початок\s+договору|договір\s+розпочав|"
            r"έναρξη\s+σύμβασης|εναρξη\s+συμβασης)"
            r"[^\d\n]{0,35}(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})",
            re.IGNORECASE,
        ),
        re.compile(
            r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})"
            r"[^\n]{0,35}(?:Vertragsbeginn|contract\s+start|تاريخ\s+بدء\s+العقد|"
            r"початок\s+договору|έναρξη\s+σύμβασης|εναρξη\s+συμβασης)",
            re.IGNORECASE,
        ),
    )
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return ""


def _arabic_companion_prefix(value: str | None, language: str) -> str | None:
    if value is None or language != "ar":
        return value
    return value.replace(
        "هذه مسودة لإلغاء العقد مع",
        "المسودة تطلب إلغاء الاشتراك مع",
        1,
    )


def _insert_contract_start(
    value: str | None,
    *,
    date_value: str,
    language: str,
) -> str | None:
    if value is None or not date_value or date_value in value:
        return value

    labels = {
        "ar": f"• تاريخ بدء العقد المذكور في النص: {date_value}.",
        "de": f"• Genannter Vertragsbeginn: {date_value}.",
        "en": f"• Stated contract start date: {date_value}.",
        "uk": f"• Зазначена дата початку договору: {date_value}.",
        "el": f"• Αναφερόμενη ημερομηνία έναρξης: {date_value}.",
    }
    anchors = {
        "ar": ("• رقم العقد أو المرجع المذكور:", "• تطلب المسودة"),
        "de": ("• Genannte Vertrags- oder Referenznummer:", "• Der Entwurf"),
        "en": ("• Stated contract or reference number:", "• The draft"),
        "uk": ("• Зазначений номер договору або посилання:", "• Чернетка"),
        "el": ("• Αναφερόμενος αριθμός σύμβασης ή αναφοράς:", "• Το προσχέδιο"),
    }
    selected = language if language in labels else "de"
    lines = value.splitlines()
    insert_at = 1 if len(lines) > 1 else len(lines)
    for anchor in anchors[selected]:
        matched = next(
            (index for index, line in enumerate(lines) if line.startswith(anchor)),
            None,
        )
        if matched is not None:
            insert_at = matched + 1
            break
    lines.insert(insert_at, labels[selected])
    return "\n".join(lines)


def build_cancellation_companion_summary(
    draft: str,
    *,
    conversation_language: str,
) -> str | None:
    return _arabic_companion_prefix(
        _base.build_cancellation_companion_summary(
            draft,
            conversation_language=conversation_language,
        ),
        conversation_language,
    )


def build_cancellation_assistance_card(
    draft: str,
    explanation: str,
    *,
    conversation_language: str,
) -> str | None:
    return _arabic_companion_prefix(
        _base.build_cancellation_assistance_card(
            draft,
            explanation,
            conversation_language=conversation_language,
        ),
        conversation_language,
    )


def build_cancellation_plain_explanation(
    draft: str,
    *,
    conversation_language: str,
) -> str | None:
    return _insert_contract_start(
        _base.build_cancellation_plain_explanation(
            draft,
            conversation_language=conversation_language,
        ),
        date_value=_contract_start_date(draft),
        language=conversation_language,
    )


def build_cancellation_missing_fields_help(
    draft: str,
    *,
    conversation_language: str,
) -> str | None:
    result = _base.build_cancellation_missing_fields_help(
        draft,
        conversation_language=conversation_language,
    )
    if result is None or conversation_language != "ar":
        return result
    return result.replace(
        "لأعبّيها معك، أرسل القيم غير الحساسة بهذه الصيغة:",
        "عدّل المسودة بهذه البيانات:",
        1,
    )


ground_cancellation_draft = _base.ground_cancellation_draft
is_cancellation_draft = _base.is_cancellation_draft
is_cancellation_request = _base.is_cancellation_request
