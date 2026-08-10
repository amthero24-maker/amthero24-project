from closed_beta_admission_service import (
    BetaAdmissionAction,
    evaluate_beta_admission,
)
from data_store import JsonDataStore


def test_disabled_gate_is_exact_pass_through(tmp_path):
    store = JsonDataStore(tmp_path / "store.json")
    outcome = evaluate_beta_admission(
        store=store,
        phone="+491701111111",
        text="مرحبا",
        language="ar",
        env={},
    )
    assert outcome.action == BetaAdmissionAction.CONTINUE
    assert outcome.decision == "disabled"
    assert store.snapshot().get("closed_beta_admissions") is None


def test_enabled_non_admitted_user_gets_notice_without_slot_consumption(tmp_path):
    store = JsonDataStore(tmp_path / "store.json")
    env = {"CLOSED_BETA_ADMISSION_ENABLED": "true"}
    outcome = evaluate_beta_admission(
        store=store,
        phone="+491701111111",
        text="مرحبا",
        language="ar",
        env=env,
    )
    assert outcome.action == BetaAdmissionAction.REPLY_AND_STOP
    assert outcome.decision == "needs_opt_in"
    assert "نسخة تجريبية" in outcome.reply
    assert "closed_beta_admissions" not in store.snapshot()


def test_decline_does_not_create_admission_record(tmp_path):
    store = JsonDataStore(tmp_path / "store.json")
    outcome = evaluate_beta_admission(
        store=store,
        phone="+491701111111",
        text="لا",
        language="ar",
        env={"CLOSED_BETA_ADMISSION_ENABLED": "true"},
    )
    assert outcome.action == BetaAdmissionAction.REPLY_AND_STOP
    assert outcome.decision == "needs_opt_in"
    assert "closed_beta_admissions" not in store.snapshot()


def test_affirmative_opt_in_claims_one_slot_then_replay_continues(tmp_path):
    store = JsonDataStore(tmp_path / "store.json")
    env = {"CLOSED_BETA_ADMISSION_ENABLED": "true", "CLOSED_BETA_ADMISSION_CAPACITY": "5"}
    first = evaluate_beta_admission(
        store=store,
        phone="+491701111111",
        text="نعم",
        language="ar",
        env=env,
    )
    assert first.action == BetaAdmissionAction.REPLY_AND_STOP
    assert first.decision == "admitted"
    assert first.changed is True

    replay = evaluate_beta_admission(
        store=store,
        phone="+491701111111",
        text="نعم",
        language="ar",
        env=env,
    )
    assert replay.action == BetaAdmissionAction.CONTINUE
    assert replay.decision == "already_admitted"


def test_full_capacity_refuses_sixth_user_without_consuming_slot(tmp_path):
    store = JsonDataStore(tmp_path / "store.json")
    env = {"CLOSED_BETA_ADMISSION_ENABLED": "true", "CLOSED_BETA_ADMISSION_CAPACITY": "5"}
    for index in range(5):
        admitted = evaluate_beta_admission(
            store=store,
            phone=f"+49170000000{index}",
            text="yes",
            language="en",
            env=env,
        )
        assert admitted.decision == "admitted"

    full = evaluate_beta_admission(
        store=store,
        phone="+491709999999",
        text="yes",
        language="en",
        env=env,
    )
    assert full.action == BetaAdmissionAction.REPLY_AND_STOP
    assert full.decision == "full"


def test_invalid_configuration_fails_closed_without_storage_side_effect(tmp_path):
    store = JsonDataStore(tmp_path / "store.json")
    outcome = evaluate_beta_admission(
        store=store,
        phone="+491701111111",
        text="yes",
        language="en",
        env={"CLOSED_BETA_ADMISSION_ENABLED": "maybe"},
    )
    assert outcome.action == BetaAdmissionAction.REPLY_AND_STOP
    assert outcome.decision == "blocked"
    assert "closed_beta_admissions" not in store.snapshot()


def test_admitted_user_passes_through_for_normal_onboarding(tmp_path):
    store = JsonDataStore(tmp_path / "store.json")
    env = {"CLOSED_BETA_ADMISSION_ENABLED": "true"}
    evaluate_beta_admission(
        store=store,
        phone="+491701111111",
        text="yes",
        language="en",
        env=env,
    )
    outcome = evaluate_beta_admission(
        store=store,
        phone="+491701111111",
        text="I need help",
        language="en",
        env=env,
    )
    assert outcome.should_continue is True
    assert outcome.reply == ""
