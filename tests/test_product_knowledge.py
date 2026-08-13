"""Regression tests for AmtHero24 product facts."""
from product_knowledge import product_answer


def test_arabic_returning_greeting_is_short_useful_and_actionable() -> None:
    result = product_answer("مرحبا", "ar")
    assert result is not None
    reply, topic = result
    assert topic == "capabilities"
    assert "سام" in reply
    assert "رسالة أو ورقة" in reply
    assert "الخطوة الجاية" in reply
    assert len(reply) < 180


def test_greeting_never_creates_a_synthetic_mission_topic() -> None:
    for previous_topic in ("", "greeting_1", "greeting_3", "contract"):
        result = product_answer("مرحبا", "ar", previous_topic)
        assert result is not None
        assert not result[1].startswith("greeting_")
    assert product_answer("مرحبا", "ar", "contract")[1] == "contract"


def test_greetings_are_localized_in_all_supported_languages() -> None:
    greetings = {"de": "Hallo", "ar": "مرحبا", "en": "hello", "uk": "привіт", "el": "γεια"}
    for language, greeting in greetings.items():
        result = product_answer(greeting, language)
        assert result is not None
        assert result[1] == "capabilities"
        assert result[0].strip()
        assert "\n" in result[0]


def test_founder_question_is_authoritative_and_never_deferred_to_the_model() -> None:
    result = product_answer("شو اسم مؤسس الشركة؟", "ar", "greeting_3")
    assert result is not None
    reply, topic = result
    assert topic == "identity"
    assert "وسام زيدان" in reply
    assert "ويسام زيدان" not in reply
    assert "غير متوفر" not in reply
    assert "ما بيكتبها وسام بنفسه" in reply


def test_natural_arabic_owner_question_uses_canonical_founder_name() -> None:
    result = product_answer("مين صاحب الشركة؟", "ar")
    assert result is not None
    reply, topic = result
    assert topic == "identity"
    assert "وسام زيدان" in reply
    assert "ويسام زيدان" not in reply


def test_founder_answer_is_localized_in_all_supported_languages() -> None:
    expected_name = {
        "ar": "وسام زيدان",
        "de": "Wissam Zidan",
        "en": "Wissam Zidan",
        "uk": "Wissam Zidan",
        "el": "Wissam Zidan",
    }
    for language, name in expected_name.items():
        result = product_answer("who is the founder of AmtHero24?", language)
        assert result is not None
        assert result[1] == "identity"
        assert name in result[0]


def test_arabic_language_question_lists_every_supported_language() -> None:
    result = product_answer("شو اللغات يلي بتحكيها؟", "ar")
    assert result is not None
    reply, topic = result
    assert topic == "languages"
    for language in ("العربية", "الألمانية", "الإنجليزية", "الأوكرانية", "اليونانية"):
        assert language in reply


def test_capability_answer_mentions_documents_and_official_german() -> None:
    result = product_answer("شو بتقدم؟", "ar")
    assert result is not None
    reply, topic = result
    assert topic == "capabilities"
    assert "مستند" in reply
    assert "بالألماني" in reply
    assert "خطوة بخطوة" in reply


def test_simple_feature_menu_command_is_supported() -> None:
    result = product_answer("الميزات", "ar")
    assert result is not None
    assert result[1] == "capabilities"
    assert "فاتورة" in result[0]


def test_short_more_followup_continues_previous_product_topic() -> None:
    language_reply = product_answer("تاني؟", "ar", "languages")
    capability_reply = product_answer("شو كمان؟", "ar", "capabilities")
    assert language_reply is not None and language_reply[1] == "languages"
    assert capability_reply is not None and capability_reply[1] == "capabilities"
    assert "اللغات المدعومة" in language_reply[0]
    assert "الموعد النهائي" in capability_reply[0]


def test_answers_are_localized_in_all_supported_languages() -> None:
    for language in ("de", "ar", "en", "uk", "el"):
        result = product_answer("what languages do you speak?", language)
        assert result is not None
        assert result[0].strip()
