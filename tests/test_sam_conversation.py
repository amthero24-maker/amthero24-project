"""Tests for Sam's deterministic conversation-stage contract."""

from sam_conversation import build_sam_conversation_contract, infer_conversation_state


def test_first_greeting_starts_at_greeting_stage() -> None:
    state = infer_conversation_state(
        text="مرحبا",
        returning_user=False,
        has_attachment=False,
        current_topic="unknown",
        mission_status="",
    )
    assert state.stage == "greeting"
    assert state.mission_status == "none"


def test_attachment_enters_organize_stage_without_executing() -> None:
    contract = build_sam_conversation_contract(
        text="",
        returning_user=False,
        has_attachment=True,
        current_topic="unknown",
        mission_status="",
    )
    assert "Current stage: organize" in contract
    assert "Never skip directly to execution" in contract


def test_active_mission_preserves_continuity() -> None:
    state = infer_conversation_state(
        text="شو صار؟",
        returning_user=True,
        has_attachment=False,
        current_topic="Kündigung",
        mission_status="in_progress",
    )
    assert state.stage == "mission"
    assert "advance the existing mission" in state.continuation


def test_waiting_mission_enters_follow_up_without_false_claims() -> None:
    contract = build_sam_conversation_contract(
        text="في رد؟",
        returning_user=True,
        has_attachment=False,
        current_topic="Widerspruch",
        mission_status="awaiting_reply",
    )
    assert "Current stage: follow_up" in contract
    assert "unless verified context says so" in contract


def test_finished_mission_closes_without_inventing_new_work() -> None:
    state = infer_conversation_state(
        text="تمام شكرا",
        returning_user=True,
        has_attachment=False,
        current_topic="Termin",
        mission_status="completed",
    )
    assert state.stage == "mission_finished"


def test_returning_greeting_without_topic_continues_relationship() -> None:
    state = infer_conversation_state(
        text="Hallo",
        returning_user=True,
        has_attachment=False,
        current_topic="unknown",
        mission_status="",
    )
    assert state.stage == "relationship_continues"


def test_unknown_status_does_not_activate_mission() -> None:
    state = infer_conversation_state(
        text="ساعدني بهالرسالة",
        returning_user=True,
        has_attachment=False,
        current_topic="Brief",
        mission_status="unexpected_external_value",
    )
    assert state.stage == "understand"


def test_short_answer_after_identity_question_preserves_context() -> None:
    state = infer_conversation_state(
        text="مكتب الضرائب",
        returning_user=True,
        has_attachment=False,
        current_topic="identity",
        mission_status="",
    )
    assert state.stage == "contextual_followup"
    assert "immediately preceding question" in state.continuation


def test_short_answer_after_capabilities_question_preserves_context() -> None:
    contract = build_sam_conversation_contract(
        text="Finanzamt",
        returning_user=True,
        has_attachment=False,
        current_topic="capabilities",
        mission_status="",
    )
    assert "Current stage: contextual_followup" in contract
    assert "dictionary or FAQ lookup" in contract
    assert "1-3 short sentences" in contract


def test_long_new_request_after_identity_is_not_forced_into_followup() -> None:
    state = infer_conversation_state(
        text="عندي رسالة من مكتب الضرائب وفيها مهلة أسبوعين وبدي أعرف شو لازم أعمل",
        returning_user=True,
        has_attachment=False,
        current_topic="identity",
        mission_status="",
    )
    assert state.stage == "understand"
