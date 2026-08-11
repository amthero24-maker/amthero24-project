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
