"""Tests for first-run onboarding and consent controls."""
from onboarding import (
    consent_decision,
    consent_prompt,
    is_enable_memory_request,
    is_memory_summary_request,
    is_simple_greeting,
    memory_summary_message,
    welcome_message,
)


def test_welcome_shows_real_value_without_pricing_pressure() -> None:
    message = welcome_message("ar")
    assert "AmtHero24" in message
    assert "فاتورة" in message
    assert "اعتراضات" in message
    assert "شو بتحب ناديلك" in message
    assert "اشتراك" not in message
    assert "مجاني" not in message


def test_consent_is_optional_specific_and_controllable() -> None:
    message = consent_prompt("ar", "وسام")
    assert "اختياري" in message
    assert "اسمك" in message
    assert "لغتك" in message
    assert "مدينتك" in message
    assert "ما بحفظ كلمات سر" in message
    assert "امسح بياناتي" in message
    assert "نعم أو لا" in message


def test_consent_commands_and_greetings() -> None:
    assert consent_decision("نعم") is True
    assert consent_decision("لا") is False
    assert consent_decision("يمكن") is None
    assert is_enable_memory_request("فعّل الذاكرة")
    assert is_memory_summary_request("شو بتعرف عني؟")
    assert is_simple_greeting("مرحبا")
    assert not is_simple_greeting("مرحبا عندي فاتورة")


def test_memory_summary_only_exposes_safe_fields() -> None:
    profile = {
        "memory_consent": "granted",
        "first_name": "وسام",
        "preferred_language": "ar",
        "city": "Düsseldorf",
        "current_topic": "invoice",
        "last_assistant_reply": "secret internal context",
    }
    answer = memory_summary_message("ar", profile)
    assert "وسام" in answer
    assert "Düsseldorf" in answer
    assert "invoice" in answer
    assert "secret internal context" not in answer
