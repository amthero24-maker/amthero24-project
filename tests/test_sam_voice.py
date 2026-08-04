from sam_voice import build_sam_voice_contract, infer_voice_state


def test_supported_languages_keep_their_voice() -> None:
    for language in ("ar", "de", "en", "uk", "el"):
        assert infer_voice_state(language_code=language, returning_user=False, has_attachment=False).language_code == language


def test_unknown_language_fails_safely_to_german() -> None:
    state = infer_voice_state(language_code="xx", returning_user=False, has_attachment=False)
    assert state.language_code == "de"


def test_returning_user_voice_continues_without_restarting() -> None:
    contract = build_sam_voice_contract(language_code="ar", returning_user=True, has_attachment=False)
    assert "Do not greet" in contract
    assert "Begin with the answer" in contract


def test_first_contact_does_not_force_marketing_intro() -> None:
    contract = build_sam_voice_contract(language_code="de", returning_user=False, has_attachment=False)
    assert "Do not use a long welcome" in contract
    assert "full capability list" in contract


def test_document_voice_uses_compact_actionable_rhythm() -> None:
    contract = build_sam_voice_contract(language_code="en", returning_user=True, has_attachment=True)
    assert "essential meaning, concrete consequence, next action" in contract
    assert "Register: official" in contract


def test_voice_contract_blocks_canned_and_manipulative_style() -> None:
    contract = build_sam_voice_contract(language_code="uk", returning_user=False, has_attachment=False)
    assert "Avoid canned openings" in contract
    assert "Avoid stacked reassurance" in contract
    assert "never end with a generic invitation" in contract


def test_voice_contract_preserves_truth_and_safety() -> None:
    contract = build_sam_voice_contract(language_code="el", returning_user=False, has_attachment=False)
    assert "never vary facts, commitments, or safety boundaries" in contract
