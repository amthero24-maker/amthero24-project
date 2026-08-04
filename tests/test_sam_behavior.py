from sam_behavior import build_sam_behavior_contract, infer_behavior_state


def test_first_contact_defaults_are_safe() -> None:
    state = infer_behavior_state(text="Hello", returning_user=False, has_attachment=False)
    assert state.mode == "first_contact"
    assert state.urgency == "normal"
    assert state.emotional_signal == "neutral"
    assert state.detail_preference == "adaptive"


def test_returning_user_continues_without_reintroduction() -> None:
    contract = build_sam_behavior_contract(
        text="What is the next step?",
        returning_user=True,
        has_attachment=False,
    )
    assert "Conversation mode: continuation" in contract
    assert "without greeting, reintroduction" in contract


def test_attachment_uses_document_mode() -> None:
    state = infer_behavior_state(text="", returning_user=False, has_attachment=True)
    assert state.mode == "document"


def test_identity_question_is_not_salesy() -> None:
    contract = build_sam_behavior_contract(
        text="من أنت ولمن تتبع؟",
        returning_user=False,
        has_attachment=False,
    )
    assert "Conversation mode: identity" in contract
    assert "do not turn it into a sales pitch" in contract


def test_stress_and_urgency_are_bounded() -> None:
    contract = build_sam_behavior_contract(
        text="أنا خايف والموضوع عاجل اليوم",
        returning_user=True,
        has_attachment=False,
    )
    assert "Urgency: high" in contract
    assert "Emotional signal: stressed" in contract
    assert "never guarantee an outcome" in contract
    assert "Do not create artificial urgency" in contract


def test_frustration_does_not_trigger_defensiveness() -> None:
    contract = build_sam_behavior_contract(
        text="أنا معصب من هالموضوع",
        returning_user=True,
        has_attachment=False,
    )
    assert "Emotional signal: frustrated" in contract
    assert "Do not defend, debate, or mirror anger" in contract


def test_user_detail_preference_is_respected() -> None:
    detailed = infer_behavior_state(
        text="اشرحلي بالتفصيل",
        returning_user=True,
        has_attachment=False,
    )
    brief = infer_behavior_state(
        text="باختصار",
        returning_user=True,
        has_attachment=False,
    )
    assert detailed.detail_preference == "detailed"
    assert brief.detail_preference == "brief"


def test_contract_forbids_generic_filler_ending() -> None:
    contract = build_sam_behavior_contract(
        text="I need help with a letter",
        returning_user=False,
        has_attachment=False,
    )
    assert "Avoid filler" in contract
    assert "not a generic offer for more help" in contract
