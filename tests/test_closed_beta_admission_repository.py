import json
import re

from closed_beta_admission import AdmissionDecision, AdmissionPolicy
from closed_beta_admission_repository import ClosedBetaAdmissionRepository


class MemoryStore:
    backend_name = "json"

    def __init__(self):
        self.data = {}

    def _transaction(self, operation):
        return operation(self.data)

    def snapshot(self):
        return json.loads(json.dumps(self.data))


def test_repository_is_disabled_without_side_effects():
    store = MemoryStore()
    repository = ClosedBetaAdmissionRepository(store)
    result = repository.claim(
        "+491700000001",
        policy=AdmissionPolicy(),
        beta_opt_in=True,
        consent_version="closed-beta-v1",
    )
    assert result.decision == AdmissionDecision.DISABLED
    assert result.verified is False
    assert store.snapshot() == {}


def test_opt_in_is_required_separately():
    store = MemoryStore()
    repository = ClosedBetaAdmissionRepository(store)
    result = repository.claim(
        "+491700000002",
        policy=AdmissionPolicy(enabled=True),
        beta_opt_in=False,
    )
    assert result.decision == AdmissionDecision.NEEDS_OPT_IN
    assert store.snapshot() == {}


def test_capacity_replay_revoke_and_reclaim_are_deterministic():
    store = MemoryStore()
    repository = ClosedBetaAdmissionRepository(store)
    policy = AdmissionPolicy(enabled=True, capacity=2)

    first = repository.claim(
        "+491700000010",
        policy=policy,
        beta_opt_in=True,
        consent_version="v1",
    )
    replay = repository.claim(
        "+491700000010",
        policy=policy,
        beta_opt_in=True,
        consent_version="v1",
    )
    second = repository.claim(
        "+491700000011",
        policy=policy,
        beta_opt_in=True,
        consent_version="v1",
    )
    full = repository.claim(
        "+491700000012",
        policy=policy,
        beta_opt_in=True,
        consent_version="v1",
    )

    assert first.decision == AdmissionDecision.ADMITTED and first.changed is True
    assert replay.decision == AdmissionDecision.ALREADY_ADMITTED and replay.changed is False
    assert second.decision == AdmissionDecision.ADMITTED
    assert full.decision == AdmissionDecision.FULL
    assert repository.status(policy).admitted_count == 2

    assert repository.revoke("+491700000010") is True
    assert repository.revoke("+491700000010") is False
    reclaimed = repository.claim(
        "+491700000012",
        policy=policy,
        beta_opt_in=True,
        consent_version="v1",
    )
    assert reclaimed.decision == AdmissionDecision.ADMITTED
    assert repository.status(policy).admitted_count == 2


def test_tenants_waves_and_recipients_are_isolated():
    store = MemoryStore()
    policy = AdmissionPolicy(enabled=True, capacity=1)
    tenant_a_wave_1 = ClosedBetaAdmissionRepository(
        store,
        tenant_key="tenant_a",
        wave="wave1",
    )
    tenant_a_wave_2 = ClosedBetaAdmissionRepository(
        store,
        tenant_key="tenant_a",
        wave="wave2",
    )
    tenant_b_wave_1 = ClosedBetaAdmissionRepository(
        store,
        tenant_key="tenant_b",
        wave="wave1",
    )
    alpha = "+491700000020"
    beta = "+491700000021"

    assert tenant_a_wave_1.claim(
        alpha,
        policy=policy,
        beta_opt_in=True,
        consent_version="v1",
    ).decision == AdmissionDecision.ADMITTED
    assert tenant_a_wave_1.claim(
        beta,
        policy=policy,
        beta_opt_in=True,
        consent_version="v1",
    ).decision == AdmissionDecision.FULL
    assert tenant_a_wave_2.claim(
        beta,
        policy=policy,
        beta_opt_in=True,
        consent_version="v1",
    ).decision == AdmissionDecision.ADMITTED
    assert tenant_b_wave_1.claim(
        beta,
        policy=policy,
        beta_opt_in=True,
        consent_version="v1",
    ).decision == AdmissionDecision.ADMITTED

    assert tenant_a_wave_1.is_admitted(alpha) is True
    assert tenant_a_wave_1.is_admitted(beta) is False
    assert tenant_a_wave_2.is_admitted(beta) is True
    assert tenant_b_wave_1.is_admitted(beta) is True

    assert tenant_a_wave_1.delete_user(beta) is True
    assert tenant_a_wave_2.is_admitted(beta) is False
    assert tenant_b_wave_1.is_admitted(beta) is True


def test_export_is_identifier_free_and_delete_covers_all_tenant_waves():
    store = MemoryStore()
    policy = AdmissionPolicy(enabled=True, capacity=2)
    tenant_a_wave_1 = ClosedBetaAdmissionRepository(
        store,
        tenant_key="tenant_a",
        wave="wave1",
    )
    tenant_a_wave_2 = ClosedBetaAdmissionRepository(
        store,
        tenant_key="tenant_a",
        wave="wave2",
    )
    tenant_b_wave_1 = ClosedBetaAdmissionRepository(
        store,
        tenant_key="tenant_b",
        wave="wave1",
    )
    phone = "+491700000030"

    for repository in (tenant_a_wave_1, tenant_a_wave_2, tenant_b_wave_1):
        assert repository.claim(
            phone,
            policy=policy,
            beta_opt_in=True,
            consent_version="closed-beta-v1",
        ).decision == AdmissionDecision.ADMITTED
    assert tenant_a_wave_2.revoke(phone) is True

    assert tenant_a_wave_1._lock_material() == tenant_a_wave_2._lock_material()
    assert tenant_a_wave_1._lock_material() != tenant_b_wave_1._lock_material()

    exported = tenant_a_wave_1.export_user_status(phone)
    assert exported["status"] == "available"
    assert exported["active"] is True
    assert [(record["wave"], record["status"]) for record in exported["records"]] == [
        ("wave1", "active"),
        ("wave2", "revoked"),
    ]
    assert all(record["consent_version"] == "closed-beta-v1" for record in exported["records"])
    serialized_export = json.dumps(exported, sort_keys=True)
    assert phone not in serialized_export
    assert "tenant_a" not in serialized_export
    assert re.search(r"\b[a-f0-9]{64}\b", serialized_export) is None

    assert tenant_a_wave_1.delete_user(phone) is True
    assert tenant_a_wave_1.export_user_status(phone) == {
        "status": "available",
        "active": False,
        "records": [],
    }
    assert tenant_b_wave_1.is_admitted(phone) is True


def test_raw_phone_is_not_stored_and_delete_is_tenant_scoped():
    store = MemoryStore()
    policy = AdmissionPolicy(enabled=True, capacity=2)
    repository = ClosedBetaAdmissionRepository(store, tenant_key="tenant_a")
    other_tenant = ClosedBetaAdmissionRepository(store, tenant_key="tenant_b")
    phone = "+491700000031"

    assert repository.claim(
        phone,
        policy=policy,
        beta_opt_in=True,
        consent_version="closed-beta-v1",
    ).decision == AdmissionDecision.ADMITTED
    assert other_tenant.claim(
        phone,
        policy=policy,
        beta_opt_in=True,
        consent_version="closed-beta-v1",
    ).decision == AdmissionDecision.ADMITTED

    serialized = json.dumps(store.snapshot(), sort_keys=True)
    assert phone not in serialized
    assert repository.delete_user(phone) is True
    assert repository.is_admitted(phone) is False
    assert other_tenant.is_admitted(phone) is True


def test_invalid_input_and_broken_storage_fail_closed():
    repository = ClosedBetaAdmissionRepository(MemoryStore())
    assert repository.claim(
        "",
        policy=AdmissionPolicy(enabled=True),
        beta_opt_in=True,
        consent_version="v1",
    ).decision == AdmissionDecision.BLOCKED
    assert repository.claim(
        "+491700000040",
        policy=AdmissionPolicy(enabled=True, capacity=0),
        beta_opt_in=True,
        consent_version="v1",
    ).decision == AdmissionDecision.BLOCKED
    assert repository.claim(
        "+491700000040",
        policy=AdmissionPolicy(enabled=True),
        beta_opt_in=True,
        consent_version="",
    ).decision == AdmissionDecision.BLOCKED

    class BrokenStore:
        backend_name = "json"

        def _transaction(self, operation):
            raise RuntimeError("synthetic")

        def snapshot(self):
            raise RuntimeError("synthetic")

    broken = ClosedBetaAdmissionRepository(BrokenStore())
    result = broken.claim(
        "+491700000041",
        policy=AdmissionPolicy(enabled=True),
        beta_opt_in=True,
        consent_version="v1",
    )
    assert result.decision == AdmissionDecision.BLOCKED
    assert result.verified is False
    assert broken.is_admitted("+491700000041") is False
    assert broken.export_user_status("+491700000041") == {
        "status": "unavailable",
        "records": [],
    }
