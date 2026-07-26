"""Regression tests for language and memory prompt rules."""

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
    assert "Do not mix German with Arabic" in prompt
    assert "For greetings, introductions, small talk, and normal questions, never use this mode" in prompt


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
