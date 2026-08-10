"""Aggregate Closed Beta monitoring and launch-gate tests."""
from __future__ import annotations

from unittest.mock import patch

from starlette.testclient import TestClient

import admin_extensions
from closed_beta_admission import AdmissionPolicy
from closed_beta_admission_repository import ClosedBetaAdmissionRepository
from closed_beta_metrics import (
    apply_closed_beta_launch_check,
    build_closed_beta_metrics,
    closed_beta_launch_check,
    contains_closed_beta_identifiers,
)
from data_store import JsonDataStore

ADMIN_TOKEN = "admin-token-closed-beta-metrics-test"


def _enabled_env(*, capacity: int = 5, tenant: str = "default", wave: str = "wave1") -> dict[str, str]:
    return {
        "CLOSED_BETA_ADMISSION_ENABLED": "true",
        "CLOSED_BETA_ADMISSION_CAPACITY": str(capacity),
        "CLOSED_BETA_TENANT_KEY": tenant,
        "CLOSED_BETA_ADMISSION_WAVE": wave,
        "CLOSED_BETA_NOTICE_VERSION": "2026-08-wave1-v1",
    }


def _claim(
    store: JsonDataStore,
    phone: str,
    *,
    tenant: str = "default",
    wave: str = "wave1",
    capacity: int = 20,
) -> None:
    repository = ClosedBetaAdmissionRepository(store, tenant_key=tenant, wave=wave)
    result = repository.claim(
        phone,
        policy=AdmissionPolicy(enabled=True, capacity=capacity),
        beta_opt_in=True,
        consent_version="2026-08-wave1-v1",
    )
    assert result.decision.value in {"admitted", "already_admitted"}


def test_json_metrics_count_only_configured_tenant_and_wave(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _claim(store, "+490000000001")
    _claim(store, "+490000000002")
    _claim(store, "+490000000003", tenant="other")
    _claim(store, "+490000000004", wave="wave2")

    metrics = build_closed_beta_metrics(store, env=_enabled_env())

    assert metrics == {
        "state": "open",
        "enabled": True,
        "verified": True,
        "wave": "wave1",
        "capacity": 5,
        "admitted_count": 2,
        "remaining_slots": 3,
        "full": False,
        "over_capacity": False,
    }
    serialized = str(metrics)
    for forbidden in (
        "+490000000001",
        "phone_hash",
        "tenant_key",
        "recipient",
        "message_text",
    ):
        assert forbidden not in serialized


def test_disabled_gate_still_monitors_existing_active_admissions(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    _claim(store, "+490000000010")

    metrics = build_closed_beta_metrics(store, env={})

    assert metrics["state"] == "disabled"
    assert metrics["enabled"] is False
    assert metrics["admitted_count"] == 1
    assert metrics["remaining_slots"] == 4
    assert closed_beta_launch_check(metrics)["status"] == "ready"


def test_full_capacity_is_safe_and_over_capacity_blocks(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    for index in range(6):
        _claim(store, f"+490000001{index:03d}", capacity=10)

    full = build_closed_beta_metrics(store, env=_enabled_env(capacity=6))
    assert full["state"] == "full"
    assert full["full"] is True
    assert full["remaining_slots"] == 0
    assert closed_beta_launch_check(full)["status"] == "ready"
    assert "stop automatically" in str(closed_beta_launch_check(full)["detail"])

    over = build_closed_beta_metrics(store, env=_enabled_env(capacity=5))
    assert over["state"] == "over_capacity"
    assert over["over_capacity"] is True
    assert closed_beta_launch_check(over)["status"] == "blocked"


def test_invalid_configuration_is_bounded_and_does_not_echo_value(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    sensitive = "synthetic-invalid-beta-flag-value"

    metrics = build_closed_beta_metrics(
        store,
        env={"CLOSED_BETA_ADMISSION_ENABLED": sensitive},
    )
    check = closed_beta_launch_check(metrics)

    assert metrics["state"] == "misconfigured"
    assert metrics["verified"] is False
    assert check["status"] == "blocked"
    assert sensitive not in str(metrics)
    assert sensitive not in str(check)


def test_launch_report_recomputes_status_with_one_beta_check() -> None:
    report = {
        "status": "ready",
        "summary": {"ready": 1, "warning": 0, "blocked": 0},
        "checks": [{"code": "postgresql", "status": "ready", "detail": "ok"}],
        "next_actions": [],
    }
    metrics = {
        "state": "over_capacity",
        "enabled": True,
        "verified": True,
        "wave": "wave1",
        "capacity": 5,
        "admitted_count": 6,
        "remaining_slots": 0,
        "full": True,
        "over_capacity": True,
    }

    result = apply_closed_beta_launch_check(report, metrics)

    assert result["status"] == "blocked"
    assert result["summary"] == {"ready": 1, "warning": 0, "blocked": 1}
    checks = [item for item in result["checks"] if item["code"] == "closed_beta_admission"]
    assert len(checks) == 1
    assert result["next_actions"]


def test_identifier_guard_rejects_tenant_and_recipient_fields() -> None:
    assert contains_closed_beta_identifiers({"tenant_key": "private"}) is True
    assert contains_closed_beta_identifiers({"nested": {"phone_hash": "private"}}) is True
    assert contains_closed_beta_identifiers({"closed_beta": {"admitted_count": 3}}) is False


def test_admin_overview_exposes_aggregate_capacity_only(tmp_path) -> None:
    store = JsonDataStore(tmp_path / "store.json")
    phone = "+490000009999"
    _claim(store, phone)
    admin_extensions.core.store = store
    admin_extensions.core._hero_memory_store = admin_extensions.core.HeroMemory(store)
    client = TestClient(admin_extensions.core.app)
    environment = {
        "ADMIN_API_TOKEN": ADMIN_TOKEN,
        **_enabled_env(),
    }

    with patch.dict("os.environ", environment, clear=True):
        response = client.get(
            "/admin/overview",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 200
    metrics = response.json()["closed_beta_admission"]
    assert metrics["state"] == "open"
    assert metrics["capacity"] == 5
    assert metrics["admitted_count"] == 1
    assert metrics["remaining_slots"] == 4
    assert phone not in response.text
    assert "phone_hash" not in response.text
    assert "tenant_key" not in response.text
