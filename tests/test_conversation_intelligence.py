"""Regression tests for language memory and conversational continuity."""
from conversation_intelligence import (
    build_effective_user_text,
    detect_language,
    explicit_language_request,
    extract_city,
    infer_topic,
)
from prompts import build_system_prompt


def test_language_detection_and_explicit_switches() -> None:
    assert detect_language("مرحبا", "de") == "ar"
    assert detect_language("", "ar") == "ar"
    assert explicit_language_request("بالعربي") == "ar"
    assert explicit_language_request("auf Deutsch") == "de"
    assert explicit_language_request("in English") == "en"


def test_short_language_followup_uses_previous_answer() -> None:
    profile = {"last_assistant_reply": "Dies ist eine Rechnung über 50 Euro."}
    effective = build_effective_user_text("بالعربي", profile)
    assert "previous answer" in effective.lower()
    assert "Arabic" in effective
    assert "Rechnung" in effective


def test_city_and_topic_are_extracted_safely() -> None:
    assert extract_city("أنا ساكن في دوسلدورف") == "دوسلدورف"
    assert extract_city("Ich wohne in Köln.") == "Köln"
    assert infer_topic("عندي مشكلة بفاتورة وقسط", "") == "invoice"
    assert infer_topic("تمام كمل", "housing") == "housing"


def test_prompt_has_human_tone_and_document_rules() -> None:
    prompt = build_system_prompt(
        sender="49123",
        text="اشرح الصورة",
        detected_language="ar",
        profile={
            "preferred_language": "ar",
            "first_name": "وسام",
            "city": "Düsseldorf",
            "current_topic": "invoice",
            "last_assistant_reply": "شرح سابق",
        },
        history=["مرحبا", "عندي فاتورة"],
        has_image=True,
    )
    assert "Reply ONLY in Arabic" in prompt
    assert "When merely explaining an incoming German document" in prompt
    assert "complete polished message in formal German" in prompt
    assert "Never reveal internal reasoning" in prompt
    assert "Known city: Düsseldorf" in prompt
