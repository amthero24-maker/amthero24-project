"""Real PostgreSQL tests for aggregate-only Closed Beta monitoring."""
from __future__ import annotations

import os
from uuid import uuid4

from closed_beta_admission import AdmissionPolicy
from closed_beta_admission_repository import ClosedBetaAdmissionRepository
from closed_beta_metrics import build_closed_beta_metrics, contains_closed_beta_identifiers
from data_store import PostgresDataStore


def _scope(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _env(tenant: str, wave: str, *, capacity: int = 5) -> dict[str, str]:
    return {
        "CLOSED_BETA_ADMISSION_ENABLED": "true",
        "CLOSED_BETA_ADMISSION_CAPACITY": str(capacity),
        "CLOSED_BETA_TENANT_KEY": tenant,
        "CLOSED_BETA_ADMISSION_WAVE": wave,
        "CLOSED_BETA_NOTICE_VERSION": "2026-08-wave1-v1",
    }


def _claim(
    repository: ClosedBetaAdmissionRepository,
    phone: str,
    *,
    capacity: int = 20,
) -> None:
    result = repository.claim(
        phone,
        policy=AdmissionPolicy(enabled=True, capacity=capacity),
        beta_opt_in=True,
        consent_version="2026-08-wave1-v1",
    )
    assert result.decision.value == "admitted"


def test_postgresql_metrics_filter_tenant_and_wave_without_identifiers() -> None:
    store = PostgresDataStore(os.environ["DATABASE_URL"])
    tenant = _scope("tenant")
    other_tenant = _scope("tenant")
    wave = _scope("wave")
    other_wave = _scope("wave")
    selected = ClosedBetaAdmissionRepository(store, tenant_key=tenant, wave=wave)
    other_tenant_repository = ClosedBetaAdmissionRepository(
        store,
        tenant_key=other_tenant,
        wave=wave,
    )
    other_wave_repository = ClosedBetaAdmissionRepository(
        store,
        tenant_key=tenant,
        wave=other_wave,
    )

    try:
        _claim(selected, "+491700000001")
        _claim(selected, "+491700000002")
        _claim(other_tenant_repository, "+491700000003")
        _claim(other_wave_repository, "+491700000004")

        metrics = build_closed_beta_metrics(store, env=_env(tenant, wave))

        assert metrics == {
            "state": "open",
            "enabled": True,
            "verified": True,
            "wave": wave,
            "capacity": 5,
            "admitted_count": 2,
            "remaining_slots": 3,
            "full": False,
            "over_capacity": False,
        }
        assert contains_closed_beta_identifiers(metrics) is False
        serialized = str(metrics)
        for forbidden in (
            tenant,
            other_tenant,
            "+491700000001",
            "phone_hash",
            "tenant_key",
        ):
            assert forbidden not in serialized
    finally:
        store.close()


def test_postgresql_metrics_detect_over_capacity_from_verified_aggregate() -> None:
    store = PostgresDataStore(os.environ["DATABASE_URL"])
    tenant = _scope("tenant")
    wave = _scope("wave")
    repository = ClosedBetaAdmissionRepository(store, tenant_key=tenant, wave=wave)

    try:
        for index in range(6):
            _claim(repository, f"+491711{index:06d}", capacity=10)

        metrics = build_closed_beta_metrics(
            store,
            env=_env(tenant, wave, capacity=5),
        )

        assert metrics["verified"] is True
        assert metrics["state"] == "over_capacity"
        assert metrics["admitted_count"] == 6
        assert metrics["capacity"] == 5
        assert metrics["remaining_slots"] == 0
        assert metrics["over_capacity"] is True
    finally:
        store.close()
