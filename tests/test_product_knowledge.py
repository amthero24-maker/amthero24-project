"""Regression tests for AmtHero24 product facts."""
from product_knowledge import product_answer


def test_arabic_returning_greeting_is_useful_and_actionable() -> None:
    result = product_answer("مرحبا", "ar")
    assert result is not None
    reply, topic = result
    assert topic == "capabilities"
    for expected in ("رسالة", "فاتورة", "عقد", "الموعد النهائي", "إلغاء", "ابعت المستند"):
        assert expected in reply
    assert "ما هو الإجراء" not in reply


def test_greetings_are_localized_in_all_supported_languages() -> None:
    greetings = {"de": "Hallo", "ar": "مرحبا", "en": "hello", "uk": "привіт", "el": "γεια"}
    for language, greeting in greetings.items():
        result = product_answer(greeting, language)
        assert result is not None
        assert result[1] == "capabilities"
        assert result[0].strip()


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
    assert "المستندات" in reply
    assert "بالألماني" in reply
    assert "شرح صغير بالعربي" in reply


def test_simple_feature_menu_command_is_supported() -> None:
    result = product_answer("الميزات", "ar")
    assert result is not None
    assert result[1] == "capabilities"
    assert "الفواتير" in result[0]


def test_short_more_followup_continues_previous_product_topic() -> None:
    language_reply = product_answer("تاني؟", "ar", "languages")
    capability_reply = product_answer("شو كمان؟", "ar", "capabilities")
    assert language_reply is not None and language_reply[1] == "languages"
    assert capability_reply is not None and capability_reply[1] == "capabilities"
    assert "اللغات الخمس" in language_reply[0]
    assert "أرتّب لك الخطوات" in capability_reply[0]


def test_answers_are_localized_in_all_supported_languages() -> None:
    for language in ("de", "ar", "en", "uk", "el"):
        result = product_answer("what languages do you speak?", language)
        assert result is not None
        assert result[0].strip()
