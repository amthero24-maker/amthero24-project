from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date

import pytest

from brief_scanner_consent_workflow import BriefScannerConsentAction
from brief_scanner_execution_boundary import (
    BriefScannerExecutionCommandKind,
    BriefScannerMissionCommand,
)
from brief_scanner_mission_planner import BriefScannerMissionKind
from brief_scanner_runtime_adapter import (
    BriefScannerRuntimeBatch,
    BriefScannerRuntimeInvocation,
    brief_scanner_runtime_idempotency_key,
)
from brief_scanner_runtime_executor import (
    BriefScannerMissionExecutionStatus,
    BriefScannerMissionRuntimeExecutor,
    BriefScannerRuntimeExecutionError,
)
from data_store import JsonDataStore


_FINGERPRINT = "a" * 64


def _mission_batch() -> BriefScannerRuntimeBatch:
    action = BriefScannerConsentAction.CREATE_MISSION
    return BriefScannerRuntimeBatch(
        planning_fingerprint=_FINGERPRINT,
        invocations=(
            BriefScannerRuntimeInvocation(
                action=action,
                idempotency_key=brief_scanner_runtime_idempotency_key(
                    _FINGERPRINT,
                    action,
                ),
                command=BriefScannerMissionCommand(
                    kind=BriefScannerExecutionCommandKind.CREATE_MISSION,
                    mission_kind=BriefScannerMissionKind.TRACK_DEADLINE,
                    title="Track document deadline",
                    topic="document",
                    next_step="Complete the required action before the deadline",
                    due_date=date(2026, 9, 1),
                ),
            ),
        ),
    )


def test_mission_batch_is_atomic_and_replay_safe(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "runtime.json")
    executor = BriefScannerMissionRuntimeExecutor(store, phone="+491111")

    first = executor(_mission_batch())
    second = executor(_mission_batch())

    assert first.status is BriefScannerMissionExecutionStatus.CREATED
    assert second.status is BriefScannerMissionExecutionStatus.REPLAYED
    assert second.mission_id == first.mission_id
    assert len(store.snapshot()["cases"]) == 1
    mission = next(iter(store.snapshot()["cases"].values()))
    assert mission["phone_hash"] != "+491111"
    assert mission["metadata"] == {
        "source": "brief_scanner",
        "category": "track_deadline",
    }


def test_same_plan_is_isolated_between_tenants(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "runtime.json")

    first = BriefScannerMissionRuntimeExecutor(store, phone="+491111")(
        _mission_batch()
    )
    second = BriefScannerMissionRuntimeExecutor(store, phone="+492222")(
        _mission_batch()
    )

    assert first.mission_id != second.mission_id
    assert len(store.snapshot()["cases"]) == 2


def test_tampering_and_unsupported_multi_action_batches_fail_before_write(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "runtime.json")
    executor = BriefScannerMissionRuntimeExecutor(store, phone="+491111")
    batch = _mission_batch()

    tampered = replace(
        batch,
        invocations=(
            replace(batch.invocations[0], idempotency_key="0" * 64),
        ),
    )
    with pytest.raises(
        BriefScannerRuntimeExecutionError,
        match="idempotency_invalid",
    ):
        executor(tampered)

    multi_action = replace(
        batch,
        invocations=batch.invocations + batch.invocations,
    )
    with pytest.raises(
        BriefScannerRuntimeExecutionError,
        match="batch_not_supported",
    ):
        executor(multi_action)

    assert store.snapshot().get("cases", {}) == {}


def test_replay_with_changed_payload_fails_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "runtime.json")
    executor = BriefScannerMissionRuntimeExecutor(store, phone="+491111")
    batch = _mission_batch()
    executor(batch)
    changed = replace(
        batch,
        invocations=(
            replace(
                batch.invocations[0],
                command=replace(
                    batch.invocations[0].command,
                    title="Changed after authorization",
                ),
            ),
        ),
    )

    with pytest.raises(ValueError, match="mission_idempotency_conflict"):
        executor(changed)

    assert len(store.snapshot()["cases"]) == 1


def test_result_is_immutable_and_invalid_tenant_is_rejected(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "runtime.json")

    for phone in ("", "not-a-phone", "++49111"):
        with pytest.raises(
            BriefScannerRuntimeExecutionError,
            match="tenant_invalid",
        ):
            BriefScannerMissionRuntimeExecutor(store, phone=phone)

    result = BriefScannerMissionRuntimeExecutor(store, phone="+491111")(
        _mission_batch()
    )
    with pytest.raises(FrozenInstanceError):
        result.mission_id = "forged"  # type: ignore[misc]
