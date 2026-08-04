from sam_personality import build_sam_personality_contract, voice_profile


def test_supported_voice_profiles_are_localized() -> None:
    assert voice_profile("ar").language_code == "ar"
    assert voice_profile("de").language_code == "de"
    assert voice_profile("en").language_code == "en"
    assert voice_profile("uk").language_code == "uk"
    assert voice_profile("el").language_code == "el"


def test_unknown_language_fails_safely_to_german() -> None:
    assert voice_profile("xx").language_code == "de"


def test_contract_contains_identity_truth_and_brand_boundaries() -> None:
    contract = build_sam_personality_contract(language_code="ar", returning_user=False)

    assert "Your name is Sam" in contract
    assert "represent AmtHero24" in contract
    assert "digital assistant" in contract
    assert "Never pretend to be a human" in contract
    assert "Never invent facts" in contract
    assert "Never claim a capability is active" in contract


def test_returning_user_contract_forbids_reintroduction() -> None:
    contract = build_sam_personality_contract(language_code="de", returning_user=True)

    assert "returning user" in contract
    assert "do not introduce yourself again" in contract


def test_first_interaction_does_not_force_repetitive_intro() -> None:
    contract = build_sam_personality_contract(language_code="en", returning_user=False)

    assert "Introduce yourself only when useful or explicitly asked" in contract


def test_contract_prohibits_manipulative_dependency() -> None:
    contract = build_sam_personality_contract(language_code="uk", returning_user=False)

    assert "Do not create emotional dependency" in contract
    assert "Never use closeness as a marketing trick" in contract
