import pytest

from closed_beta_admission import (
    AdmissionDecision,
    AdmissionPolicy,
    AdmissionSnapshot,
    aggregate_status,
    decide_admission,
    privacy_safe_event_names,
    released_count,
)


def test_admission_is_disabled_by_default():
    assert decide_admission(AdmissionPolicy(), AdmissionSnapshot(0, beta_opt_in=True)) == AdmissionDecision.DISABLED


def test_opt_in_is_required_separately_before_admission():
    policy = AdmissionPolicy(enabled=True, capacity=5)
    assert decide_admission(policy, AdmissionSnapshot(0, beta_opt_in=False)) == AdmissionDecision.NEEDS_OPT_IN
    assert decide_admission(policy, AdmissionSnapshot(0, beta_opt_in=True)) == AdmissionDecision.ADMITTED


def test_capacity_fails_closed_at_five_for_wave_one():
    policy = AdmissionPolicy(enabled=True, capacity=5)
    assert decide_admission(policy, AdmissionSnapshot(4, beta_opt_in=True)) == AdmissionDecision.ADMITTED
    assert decide_admission(policy, AdmissionSnapshot(5, beta_opt_in=True)) == AdmissionDecision.FULL


def test_replay_for_existing_participant_does_not_consume_another_slot():
    policy = AdmissionPolicy(enabled=True, capacity=5)
    snapshot = AdmissionSnapshot(5, already_admitted=True, beta_opt_in=True)
    assert decide_admission(policy, snapshot) == AdmissionDecision.ALREADY_ADMITTED


@pytest.mark.parametrize(
    "snapshot",
    (
        AdmissionSnapshot(0, beta_opt_in=True, database_available=False),
        AdmissionSnapshot(0, beta_opt_in=True, state_verified=False),
        AdmissionSnapshot(0, beta_opt_in=True, safety_hold=True),
    ),
)
def test_unverifiable_or_unsafe_state_fails_closed(snapshot):
    assert decide_admission(AdmissionPolicy(enabled=True), snapshot) == AdmissionDecision.BLOCKED


def test_invalid_configuration_and_counts_fail_closed():
    with pytest.raises(ValueError, match="beta_capacity_invalid"):
        decide_admission(AdmissionPolicy(enabled=True, capacity=0), AdmissionSnapshot(0, beta_opt_in=True))
    with pytest.raises(ValueError, match="beta_admitted_count_invalid"):
        decide_admission(AdmissionPolicy(enabled=True), AdmissionSnapshot(-1, beta_opt_in=True))
    with pytest.raises(ValueError, match="beta_admitted_count_exceeds_capacity"):
        decide_admission(AdmissionPolicy(enabled=True, capacity=5), AdmissionSnapshot(6, beta_opt_in=True))


def test_aggregate_status_contains_counts_only():
    status = aggregate_status(AdmissionPolicy(enabled=True, capacity=5), AdmissionSnapshot(3, beta_opt_in=True))
    assert status.capacity == 5
    assert status.admitted_count == 3
    assert status.remaining_slots == 2
    assert status.full is False
    assert not hasattr(status, "phone")
    assert not hasattr(status, "user_id")


def test_leave_release_is_idempotent_at_policy_boundary():
    assert released_count(3, was_admitted=True) == 2
    assert released_count(3, was_admitted=False) == 3
    assert released_count(0, was_admitted=True) == 0


def test_privacy_safe_events_have_no_user_payload_fields():
    events = privacy_safe_event_names()
    assert "beta_admission_granted" in events
    assert all("phone" not in event and "message" not in event and "document" not in event for event in events)
