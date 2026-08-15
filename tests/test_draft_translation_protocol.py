"""Regressions for indexed full-translation assistance and fail-closed UX."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from conversation_intelligence import detect_language
from data_store import JsonDataStore
from draft_assistance import (
    ASSISTANCE_END_MARKER,
    ASSISTANCE_MARKER,
    ASSISTANCE_TRANSLATE,
    activate_draft_assistance,
    reset_draft_assistance,
)
from draft_translation_protocol import (
    activate_translation_protocol,
    build_indexed_translation_source,
    build_translation_prompt_contract,
    parse_indexed_translation_reply,
    reset_translation_protocol,
    translation_line_marker,
)
from official_draft_runtime_extension import install

_DRAFT = """MusterFit GmbH
[Adresse des Anbieters, falls bekannt]
[Postleitzahl und Ort]

[Ihr Vor- und Nachname]
[Ihre Straße und Hausnummer]
[Ihre Postleitzahl und Ort]

[Datum]

Betreff: Kündigung des Mitgliedsvertrags – Vertragsnummer TEST-K-731

Sehr geehrte Damen und Herren,

hiermit kündige ich meinen Mitgliedsvertrag mit der Vertragsnummer TEST-K-731 zum nächstmöglichen Zeitpunkt.

Bitte bestätigen Sie mir schriftlich das Datum, zu dem das Vertragsverhältnis endet.

Mit freundlichen Grüßen
[Ihr Name]"""

_TRANSLATED_LINES = (
    "MusterFit GmbH",
    "[عنوان مقدم الخدمة، إن كان معروفًا]",
    "[الرمز البريدي والمدينة]",
    "",
    "[الاسم الأول واسم العائلة]",
    "[الشارع ورقم المنزل]",
    "[الرمز البريدي والمدينة]",
    "",
    "[التاريخ]",
    "",
    "الموضوع: إلغاء عقد العضوية – رقم العقد TEST-K-731",
    "",
    "السادة المحترمون،",
    "",
    "بموجب هذه الرسالة ألغي عقد عضويتي ذي الرقم TEST-K-731 في أقرب موعد ممكن.",
    "",
    "يرجى تأكيد التاريخ الذي تنتهي فيه العلاقة التعاقدية خطيًا.",
    "",
    "مع خالص التحية",
    "[اسمك]",
)

_BLANK = "<<<AMTHERO24_BLANK_LINE>>>"


def _indexed_reply(
    translated_lines: tuple[str, ...] = _TRANSLATED_LINES,
    *,
    ids: tuple[int, ...] | None = None,
) -> str:
    selected_ids = ids or tuple(range(1, len(translated_lines) + 1))
    lines: list[str] = []
    for index, translated in zip(selected_ids, translated_lines, strict=True):
        payload = translated if translated else _BLANK
        lines.append(f"{translation_line_marker(_DRAFT, 'ar', index)} {payload}")
    return (
        f"{ASSISTANCE_MARKER}\n"
        + "\n".join(lines)
        + f"\n{ASSISTANCE_END_MARKER}"
    )


def _parse(value: str):
    assistance_token = activate_draft_assistance(
        action=ASSISTANCE_TRANSLATE,
        draft=_DRAFT,
        conversation_language="ar",
    )
    translation_token = activate_translation_protocol(
        draft=_DRAFT,
        conversation_language="ar",
    )
    try:
        return parse_indexed_translation_reply(
            value,
            draft=_DRAFT,
            conversation_language="ar",
        )
    finally:
        reset_translation_protocol(translation_token)
        reset_draft_assistance(assistance_token)


def _seed(store: JsonDataStore) -> None:
    store.update_user("49123", {
        "memory_consent": "granted",
        "memory_consent_at": datetime.now(UTC).isoformat(),
        "memory_consent_version": "test-v1",
        "onboarding_stage": "complete",
        "preferred_language": "ar",
        "session_language": "ar",
        "session_last_reply": _DRAFT,
        "last_assistant_reply": "safe prior reusable reply",
        "session_topic": "cancellation",
        "current_topic": "cancellation",
        "conversation_summary": "safe prior summary",
    })


def _core(store: JsonDataStore, *, reply: str, send: AsyncMock) -> SimpleNamespace:
    core = SimpleNamespace(
        store=store,
        detect_language=detect_language,
        send_whatsapp_message=send,
    )

    async def process_incoming(message) -> None:
        await core.send_whatsapp_message(message.sender, reply)
        store.update_user(message.sender, {
            "session_last_reply": reply,
            "last_assistant_reply": reply,
            "session_topic": "synthetic",
            "current_topic": "synthetic",
            "conversation_summary": "synthetic translation state",
        })
        store.update_message_status(message.message_id, "sent")

    core.process_incoming = process_incoming
    return core


def test_prompt_indexes_every_source_line_and_blank_paragraph() -> None:
    token = activate_translation_protocol(
        draft=_DRAFT,
        conversation_language="ar",
    )
    try:
        contract = build_translation_prompt_contract()
    finally:
        reset_translation_protocol(token)

    encoded = build_indexed_translation_source(_DRAFT, "ar")
    assert len(encoded) == len(_DRAFT.splitlines())
    assert "Translate the complete source draft faithfully into Arabic" in contract
    assert "Never omit, duplicate, merge, split, reorder, or invent a marker" in contract
    assert _BLANK in contract
    assert encoded[0] in contract
    assert encoded[-1] in contract


def test_complete_indexed_translation_is_reconstructed_without_private_markers() -> None:
    result = _parse(_indexed_reply())

    assert result.rejection_reason is None
    assert result.protocol_detected is True
    assert result.text is not None
    assert result.text.startswith("ترجمة للفهم فقط")
    assert "MusterFit GmbH" in result.text
    assert "TEST-K-731" in result.text
    assert "السادة المحترمون" in result.text
    assert "يرجى تأكيد التاريخ" in result.text
    assert "[عنوان مقدم الخدمة، إن كان معروفًا]" in result.text
    assert "[اسمك]" in result.text
    assert "AMTHERO24_TRANSLATION" not in result.text
    assert _BLANK not in result.text


@pytest.mark.parametrize(
    ("ids", "expected_reason"),
    (
        (tuple(range(1, 20)), "missing-line-id"),
        ((1, 2, 2) + tuple(range(4, 21)), "duplicate-line-id"),
        ((2, 1) + tuple(range(3, 21)), "reordered-line-id"),
        (tuple(range(1, 20)) + (999,), "unknown-line-id"),
    ),
)
def test_missing_duplicate_reordered_or_unknown_ids_fail_closed(
    ids: tuple[int, ...],
    expected_reason: str,
) -> None:
    translated = _TRANSLATED_LINES[: len(ids)]
    result = _parse(_indexed_reply(translated, ids=ids))

    assert result.text is None
    assert result.protocol_detected is True
    assert result.rejection_reason == expected_reason


def test_nonindexed_summary_is_not_mistaken_for_protocol_output() -> None:
    value = f"""{ASSISTANCE_MARKER}
هذه الرسالة نموذج إلغاء. عدّل بياناتك ثم أرسلها.
{ASSISTANCE_END_MARKER}"""
    result = _parse(value)

    assert result.text is None
    assert result.protocol_detected is False
    assert result.rejection_reason == "protocol-not-detected"


@pytest.mark.anyio
async def test_runtime_delivers_complete_indexed_translation_and_preserves_draft(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _seed(store)
    store.claim_message("translate-indexed-1", "49123", "1")
    send = AsyncMock()
    core = _core(store, reply=_indexed_reply(), send=send)
    install(core)

    message = SimpleNamespace(
        message_id="translate-indexed-1",
        sender="49123",
        text="1",
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    send.assert_awaited_once()
    delivered = send.await_args.args[1]
    assert delivered.startswith("ترجمة للفهم فقط")
    assert "يرجى تأكيد التاريخ" in delivered
    assert "AMTHERO24_TRANSLATION" not in delivered
    profile = store.get_user("49123")
    assert profile["session_last_reply"] == _DRAFT
    assert profile["last_assistant_reply"] == "safe prior reusable reply"
    assert profile["session_topic"] == "cancellation"
    assert profile["current_topic"] == "cancellation"
    assert profile["conversation_summary"] == "safe prior summary"
    assert store.snapshot()["messages"]["translate-indexed-1"]["status"] == "sent"


@pytest.mark.anyio
async def test_runtime_uses_localized_safe_failure_instead_of_generic_error(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _seed(store)
    store.claim_message("translate-invalid-1", "49123", "1")
    send = AsyncMock()
    incomplete_ids = tuple(range(1, len(_TRANSLATED_LINES)))
    bad_reply = _indexed_reply(_TRANSLATED_LINES[:-1], ids=incomplete_ids)
    core = _core(store, reply=bad_reply, send=send)
    install(core)

    message = SimpleNamespace(
        message_id="translate-invalid-1",
        sender="49123",
        text="1",
        message_type="text",
        internal_context="",
    )
    await core.process_incoming(message)

    send.assert_awaited_once()
    delivered = send.await_args.args[1]
    assert "ما قدرت أتأكد أن الترجمة كاملة" in delivered
    assert "المسودة الأصلية بقيت محفوظة بدون تغيير" in delivered
    assert "خلل تقني" not in delivered
    assert "AMTHERO24_TRANSLATION" not in delivered
    profile = store.get_user("49123")
    assert profile["session_last_reply"] == _DRAFT
    assert profile["last_assistant_reply"] == "safe prior reusable reply"
    assert profile["session_topic"] == "cancellation"
    assert profile["current_topic"] == "cancellation"
    assert profile["conversation_summary"] == "safe prior summary"
    assert store.snapshot()["messages"]["translate-invalid-1"]["status"] == "sent"
