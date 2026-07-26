"""Relationship Engine tests."""
from relationship_engine import (
    analyze_preferences,
    augment_prompt,
    detect_mood_signal,
    human_memory_summary,
    parse_style,
    serialize_style,
)


def test_explicit_long_term_preferences_are_compact_and_reversible() -> None:
    result = analyze_preferences("من هلق جاوبني باختصار واحكي معي عالسوري")
    assert result.changed["detail"] == "concise"
    assert result.changed["dialect"] == "levantine"
    assert result.persistent is True
    encoded = serialize_style(result.settings)
    assert len(encoded) <= 80
    assert parse_style(encoded)["detail"] == "concise"


def test_single_document_request_is_not_mistaken_for_permanent_preference() -> None:
    result = analyze_preferences("اختصرلي هالإيميل الألماني")
    assert result.persistent is False
    assert result.command_only is False


def test_mood_is_temporary_conversation_signal() -> None:
    assert detect_mood_signal("أنا مستعجل وضروري خلص اليوم") == "urgent"
    assert detect_mood_signal("ما فهمت شي ومحتار") == "confused"


def test_prompt_adapts_without_claiming_human_identity() -> None:
    prompt = augment_prompt(
        "BASE",
        profile={"communication_style": "detail=concise;tone=friendly;dialect=levantine"},
        text="أنا متوتر شوي",
        history=[],
    )
    assert "Preferred response detail: concise" in prompt
    assert "Current conversational signal: stressed" in prompt
    assert "never infer ethnicity" in prompt
    assert "Do not manufacture intimacy" in prompt


def test_memory_summary_uses_human_labels_not_internal_codes() -> None:
    answer = human_memory_summary("ar", {
        "memory_consent": "granted",
        "first_name": "وسام",
        "preferred_language": "ar",
        "current_topic": "languages",
        "communication_style": "detail=concise;dialect=levantine",
    })
    assert "العربية" in answer
    assert "اللغات التي يدعمها AmtHero24" in answer
    assert "مختصر ومباشر" in answer
    assert "languages" not in answer
    assert "لغتك: ar" not in answer
