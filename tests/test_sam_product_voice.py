"""Acceptance tests for Sam's confident personal-assistant product voice."""
from __future__ import annotations

from types import SimpleNamespace

import product_knowledge as base
import sam_product_voice as voice


def test_natural_arabic_identity_is_value_led_not_defensive() -> None:
    result = voice.product_answer("مين انت؟", "ar")
    assert result is not None
    reply, topic = result
    assert topic == "identity"
    assert "مساعدك الشخصي" in reply
    assert "AmtHero24" in reply
    assert "عم يتطور" in reply
    assert "مساعد رقمي" not in reply
    assert "لست إنسان" not in reply


def test_arabic_capabilities_cover_current_daily_assistant_scope() -> None:
    result = voice.product_answer("شو بتعمل وشو خدماتك؟", "ar")
    assert result is not None
    reply, topic = result
    assert topic == "capabilities"
    for expected in (
        "PDF", "رسائل وإيميلات", "الإلغاء", "العقود", "الاسترداد",
        "المواعيد", "التذكيرات", "الذاكرة", "العربي", "الألماني",
    ):
        assert expected in reply
    assert "سؤال وجواب وخلاص" in reply
    assert "عم يتطور" in reply


def test_combined_identity_capability_founder_question_gets_complete_answer() -> None:
    result = voice.product_answer("مين انت وشو بتعمل ومين عملك وصنعك؟", "ar")
    assert result is not None
    reply, topic = result
    assert topic == "identity"
    assert "Wissam Zidan" in reply
    assert "مساعدك الشخصي" in reply
    assert "مو مجرد محادثة سؤال وجواب" in reply
    assert "التذكيرات" in reply
    assert "عم يتطور" in reply


def test_explicit_ai_identity_stays_transparent_and_delegates_to_authoritative_answer() -> None:
    result = voice.product_answer("هل انت ChatGPT؟", "ar")
    assert result == base.product_answer("هل انت ChatGPT؟", "ar")
    assert result is not None
    assert "ChatGPT" in result[0]


def test_natural_identity_is_localized_in_all_supported_languages() -> None:
    prompts = {
        "ar": "مين انت؟",
        "de": "Wer bist du?",
        "en": "Who are you?",
        "uk": "Хто ти?",
        "el": "Ποιος εισαι?",
    }
    forbidden = {
        "ar": "لست إنسان",
        "de": "kein Mensch",
        "en": "not a human",
        "uk": "не людина",
        "el": "όχι άνθρωπος",
    }
    for language, prompt in prompts.items():
        result = voice.product_answer(prompt, language)
        assert result is not None
        assert result[1] == "identity"
        assert "AmtHero24" in result[0]
        assert forbidden[language].casefold() not in result[0].casefold()


def test_install_replaces_only_core_product_answer_reference() -> None:
    original = lambda *_args, **_kwargs: ("original", "identity")
    core = SimpleNamespace(product_answer=original)
    voice._INSTALLED = False
    voice._ORIGINAL_PRODUCT_ANSWER = None
    voice.install(core)
    assert core.product_answer is voice.product_answer
