from __future__ import annotations

import pytest

from prompts import build_system_prompt


LANGUAGE_REQUESTS = {
    "de": "Schreib mir bitte eine E-Mail an die Versicherung.",
    "ar": "اكتبلي ايميل للتأمين.",
    "en": "Write an email to the insurance company.",
    "uk": "Напиши електронного листа страховій компанії.",
    "el": "Γράψε ένα email στην ασφαλιστική εταιρεία.",
}


@pytest.mark.parametrize("language", sorted(LANGUAGE_REQUESTS))
def test_official_writing_contract_is_present_for_every_supported_conversation_language(language: str) -> None:
    prompt = build_system_prompt(
        sender="491234567890",
        text=LANGUAGE_REQUESTS[language],
        detected_language=language,
        profile={"preferred_language": language},
        history=[],
        has_image=False,
    )

    assert "Identify the recipient and purpose from verified user/document facts" in prompt
    assert "ask one focused clarification before drafting" in prompt
    assert "Use only facts actually supplied by the user or a trusted document-analysis result" in prompt
    assert "Never invent names, addresses, dates, reference numbers, amounts" in prompt
    assert "[Ihr Name]" in prompt
    assert "Make the draft reviewable" in prompt
    assert "do not claim it was sent, submitted, accepted, approved, or delivered" in prompt
    assert "Sending is a separate user-authorized action boundary" in prompt
    assert "use the corrected value" in prompt
    assert "do not preserve the superseded value" in prompt
    assert "Distinguish suggested wording from legal advice" in prompt


@pytest.mark.parametrize(
    ("language", "request"),
    [
        ("de", "Schreib eine Klage und garantiere, dass ich vor Gericht gewinne."),
        ("ar", "اكتب دعوى محكمة وضمنلي اني اربح."),
        ("en", "Write my court lawsuit and guarantee that I win."),
        ("uk", "Напиши судовий позов і гарантуй, що я виграю."),
        ("el", "Γράψε αγωγή και εγγυήσου ότι θα κερδίσω στο δικαστήριο."),
    ],
)
def test_high_risk_writing_requests_receive_explicit_fail_safe_guidance(language: str, request: str) -> None:
    prompt = build_system_prompt(
        sender="491234567890",
        text=request,
        detected_language=language,
        profile={"preferred_language": language},
        history=[],
        has_image=False,
    )

    assert "Risk level: high" in prompt
    assert "do not produce confident autonomous legal/medical strategy" in prompt
    assert "State the limitation and direct the user to appropriate qualified help" in prompt
    assert "never add unsupported threats, guarantees, legal claims, or rights" in prompt


def test_unknown_profile_fields_remain_unknown_in_writing_context() -> None:
    prompt = build_system_prompt(
        sender="491234567890",
        text="Write a formal request for an appointment.",
        detected_language="en",
        profile={},
        history=[],
        has_image=False,
    )

    assert "Known first name: unknown" in prompt
    assert "Known city: unknown" in prompt
    assert "If a fact is unknown, do not invent it" in prompt
