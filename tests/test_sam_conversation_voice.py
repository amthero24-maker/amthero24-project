import types

import sam_conversation_voice as voice


def test_german_name_question_no_longer_uses_cold_digital_disclaimer():
    answer, topic = voice.product_answer("Wie heißt du denn?", "de")
    assert topic == "identity"
    assert "Ich heiße Sam" in answer
    assert "kein Mensch" not in answer
    assert "Was kostet dich" in answer


def test_arabic_identity_is_personal_and_invites_next_turn():
    answer, topic = voice.product_answer("مين انت", "ar")
    assert topic == "identity"
    assert "مساعدك الشخصي" in answer
    assert "شو بتحب" in answer
    assert "لست إنسان" not in answer


def test_capabilities_end_with_real_next_action_not_faq_dead_end():
    answer, topic = voice.product_answer("شو خدماتك وميزاتك كلها", "ar")
    assert topic == "capabilities"
    assert "PDF" in answer
    assert "التذكيرات" in answer
    assert "ابعتلي شغلة حقيقية" in answer


def test_explicit_ai_identity_still_delegates_to_existing_transparency():
    answer, topic = voice.product_answer("هل أنت ChatGPT؟", "ar")
    assert topic == "identity"
    assert "ChatGPT" in answer
    assert "AmtHero24" in answer


def test_install_places_conversation_layer_last():
    core = types.SimpleNamespace(product_answer=lambda *args: None)
    voice.install(core)
    assert core.product_answer is voice.product_answer
