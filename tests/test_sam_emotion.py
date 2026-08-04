from sam_emotion import build_sam_emotion_contract, infer_emotion_state


def test_neutral_fallback_does_not_invent_emotion() -> None:
    state = infer_emotion_state("Please explain this letter")
    assert state.signal == "neutral"
    assert state.confidence == "none"


def test_explicit_confusion_is_detected_across_languages() -> None:
    assert infer_emotion_state("مش فاهم شو يعني هاد").signal == "confused"
    assert infer_emotion_state("Ich verstehe nicht, was das bedeutet").signal == "confused"
    assert infer_emotion_state("I don't understand this").signal == "confused"


def test_explicit_worry_and_frustration_are_bounded() -> None:
    assert infer_emotion_state("أنا خايف من المهلة").signal == "worried"
    assert infer_emotion_state("Ich bin genervt, das klappt nie").signal == "frustrated"


def test_positive_and_relief_signals_are_not_overplayed() -> None:
    assert infer_emotion_state("ممتاز، هلق فهمت").signal in {"relieved", "positive"}
    contract = build_sam_emotion_contract(text="ارتحت، شكراً")
    assert "over-celebrating" in contract


def test_contract_prohibits_diagnosis_storage_and_manipulation() -> None:
    contract = build_sam_emotion_contract(text="I am worried")
    assert "Do not diagnose" in contract
    assert "Do not use empathy as persuasion" in contract
    assert "Keep emotional acknowledgement shorter than the practical help" in contract
