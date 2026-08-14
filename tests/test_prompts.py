"""Regression tests for language, memory, and copy-safe prompt rules."""

from official_draft_delivery import DRAFT_MARKER, END_MARKER, EXPLANATION_MARKER
from prompts import build_system_prompt


def test_arabic_normal_chat_forbids_mixed_translation_mode() -> None:
    prompt = build_system_prompt(
        sender="49123",
        text="مرحبا",
        detected_language="ar",
        profile={},
        history=[],
        has_image=False,
    )
    assert "Reply ONLY in Arabic" in prompt
    assert "Do not mix languages in ordinary conversation" in prompt
    assert "except when drafting an official German letter or email" in prompt
    assert "When merely explaining an incoming German document or image" in prompt
    assert DRAFT_MARKER not in prompt
    assert EXPLANATION_MARKER not in prompt
    assert END_MARKER not in prompt


def test_arabic_status_phrase_does_not_become_known_name() -> None:
    prompt = build_system_prompt(
        sender="49123",
        text="انا جديد هون",
        detected_language="ar",
        profile={"first_name": "جديد", "preferred_language": "ar"},
        history=["مرحبا"],
        has_image=False,
    )
    assert "Known first name: unknown" in prompt
    assert "Known first name: جديد" not in prompt


def test_real_name_remains_available() -> None:
    prompt = build_system_prompt(
        sender="49123",
        text="شو اسمي؟",
        detected_language="ar",
        profile={"first_name": "وسام", "preferred_language": "ar"},
        history=[],
        has_image=False,
    )
    assert "Known first name: وسام" in prompt


def test_official_cancellation_draft_uses_private_split_envelope() -> None:
    prompt = build_system_prompt(
        sender="49123",
        text="اكتبلي رسالة إلغاء بالألماني ولا ترسلها.",
        detected_language="ar",
        profile={"preferred_language": "ar", "onboarding_stage": "complete"},
        history=[],
        has_image=False,
    )
    assert "COPY-SAFE OFFICIAL DRAFT DELIVERY — ACTIVE" in prompt
    assert DRAFT_MARKER in prompt
    assert EXPLANATION_MARKER in prompt
    assert END_MARKER in prompt
    assert "no `Entwurf`/`Draft` heading" in prompt
    assert "never append explanation, translation, or next-step guidance under the draft" in prompt


def test_ephemeral_runtime_flag_keeps_revision_prompt_copy_safe() -> None:
    prompt = build_system_prompt(
        sender="49123",
        text="Restate the previous answer in German only.",
        detected_language="ar",
        profile={
            "preferred_language": "ar",
            "_official_draft_delivery_active": True,
        },
        history=[],
        has_image=False,
    )
    assert DRAFT_MARKER in prompt
    assert EXPLANATION_MARKER in prompt
    assert END_MARKER in prompt
