from mvp_runtime_contract import build_mvp_runtime_contract
from prompts import build_system_prompt


def test_contract_names_all_six_launch_journeys_and_external_boundary():
    contract = build_mvp_runtime_contract()
    for marker in (
        "BRIEF SCANNER",
        "OFFICIAL LETTERS & EMAILS",
        "KÜNDIGUNG / CANCELLATION",
        "VERTRAGS-CHECK / CONTRACT CHECK",
        "GELD ZURÜCK / REFUND",
        "TERMIN ASSISTANCE",
    ):
        assert marker in contract
    assert "never claim an external action was executed" in contract
    assert "never claim an appointment was booked, moved or cancelled" in contract
    assert "Never promise a refund" in contract
    assert "do not claim the contract is cancelled" in contract


def test_system_prompt_includes_runtime_contract_without_enabling_execution(monkeypatch):
    monkeypatch.delenv("BRIEF_SCANNER_CANARY_SENDERS", raising=False)
    prompt = build_system_prompt(
        sender="491000000000",
        text="Ich möchte meinen Vertrag kündigen.",
        detected_language="de",
        profile={},
        history=[],
        has_image=False,
    )
    assert "SIX MVP JOURNEY RUNTIME CONTRACT" in prompt
    assert "KÜNDIGUNG / CANCELLATION" in prompt
    assert "GELD ZURÜCK / REFUND" in prompt
    assert "TERMIN ASSISTANCE" in prompt
    assert "External execution is always a separate explicit boundary" in prompt


def test_contract_requires_exact_appointment_facts_and_truthful_contract_review():
    contract = build_mvp_runtime_contract()
    assert "never infer a missing time or place" in contract
    assert "officially required documents from optional preparation suggestions" in contract
    assert "Never invent missing clauses" in contract
    assert "legally valid/invalid" in contract


def test_contract_preserves_user_corrections_and_high_risk_boundary():
    contract = build_mvp_runtime_contract()
    assert "User corrections replace superseded facts" in contract
    assert "court litigation" in contract
    assert "asylum/deportation strategy" in contract
    assert "medical emergencies" in contract


def test_brief_scanner_contract_preserves_exact_sender_and_identifier_meaning():
    contract = build_mvp_runtime_contract()
    assert "exact visible sender organization" in contract
    assert "do not replace its business/legal name with a generic category" in contract
    assert "Treat every customer number, contract number, invoice number, case number and reference number as an identifier only" in contract
    assert "Never instruct the user to use one as a bank-transfer reference or payment purpose unless the document explicitly assigns that use" in contract


def test_brief_scanner_contract_does_not_invent_payment_details_or_urgency(monkeypatch):
    monkeypatch.delenv("BRIEF_SCANNER_CANARY_SENDERS", raising=False)
    prompt = build_system_prompt(
        sender="491000000000",
        text=(
            "Musterstadt Energie GmbH\n"
            "Betreff: Offene Rechnung\n"
            "Kundennummer: TEST-4821\n"
            "Betrag: 48,50 EUR\n"
            "Zahlungsfrist: 28.08.2026"
        ),
        detected_language="ar",
        profile={"preferred_language": "ar"},
        history=[],
        has_image=False,
    )
    assert "Preserve the exact stated deadline" in prompt
    assert "do not add urgency such as immediately or as soon as possible unless the document states it" in prompt
    assert "does not contain bank details or an explicit payment purpose" in prompt
    assert "request the missing page, instead of inventing them" in prompt
