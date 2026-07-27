from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, time

import pytest

from brief_scanner_consent_workflow import (
    BriefScannerConsentChoice,
    BriefScannerConsentDecision,
    plan_brief_scanner_consent,
    record_brief_scanner_consent,
)
from brief_scanner_contract import BriefScannerFacts
from brief_scanner_execution_boundary import build_brief_scanner_execution_envelope
from brief_scanner_mission_planner import compose_brief_scanner_mission_plan
from brief_scanner_runtime_adapter import (
    BriefScannerRuntimeGates,
    BriefScannerRuntimeStatus,
    dispatch_brief_scanner_runtime,
)


def _envelope(*, decline_all: bool = False):
    bundle = compose_brief_scanner_mission_plan(
        BriefScannerFacts(
            language="de",
            readable=True,
            sender_organization="Synthetic Authority",
            requested_action="send documents",
            deadline=date(2026, 9, 1),
        ),
        response_instruction="Ask for a two-week extension.",
    )
    assert bundle is not None
    plan = plan_brief_scanner_consent(
        bundle,
        memory_consent_active=True,
        reminder_delivery_time=time(9, 30),
        reminder_timezone_name="Europe/Berlin",
    )
    assert plan is not None
    receipt = record_brief_scanner_consent(
        plan,
        tuple(
            BriefScannerConsentChoice(
                action=action,
                decision=(
                    BriefScannerConsentDecision.DECLINE
                    if decline_all
                    else BriefScannerConsentDecision.APPROVE
                ),
            )
            for action in plan.requested_actions
        ),
    )
    envelope = build_brief_scanner_execution_envelope(
        bundle,
        plan,
        receipt,
        reminder_lead_days=None if decline_all else 3,
        now=datetime(2026, 8, 1, tzinfo=UTC),
    )
    return envelope, receipt.planning_fingerprint


def _all_gates() -> BriefScannerRuntimeGates:
    return BriefScannerRuntimeGates(
        runtime_enabled=True,
        mission_enabled=True,
        draft_enabled=True,
        reminder_enabled=True,
    )


def test_missing_environment_gates_disable_dispatch(monkeypatch) -> None:
    for name in (
        "BRIEF_SCANNER_RUNTIME_ENABLED",
        "BRIEF_SCANNER_RUNTIME_MISSION_ENABLED",
        "BRIEF_SCANNER_RUNTIME_DRAFT_ENABLED",
        "BRIEF_SCANNER_RUNTIME_REMINDER_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    envelope, fingerprint = _envelope()

    def must_not_run(_batch):
        raise AssertionError("executor must remain unreachable while runtime is disabled")

    result = dispatch_brief_scanner_runtime(
        envelope,
        authorization_fingerprint=fingerprint,
        executor=must_not_run,
    )

    assert result.status == BriefScannerRuntimeStatus.DISABLED
    assert result.dispatched is False
    assert result.side_effects_permitted is False
    assert result.blocked_actions == envelope.approved_actions


def test_every_required_action_gate_is_checked_before_dispatch() -> None:
    envelope, fingerprint = _envelope()
    calls = []

    result = dispatch_brief_scanner_runtime(
        envelope,
        authorization_fingerprint=fingerprint,
        gates=replace(_all_gates(), draft_enabled=False),
        executor=lambda batch: calls.append(batch),
    )

    assert result.status == BriefScannerRuntimeStatus.BLOCKED_BY_ACTION_GATE
    assert tuple(action.value for action in result.blocked_actions) == ("generate_draft",)
    assert calls == []


def test_complete_opt_in_dispatches_one_ordered_atomic_batch() -> None:
    envelope, fingerprint = _envelope()
    batches = []

    def executor(batch):
        batches.append(batch)
        return {"batch_id": "synthetic"}

    result = dispatch_brief_scanner_runtime(
        envelope,
        authorization_fingerprint=fingerprint,
        gates=_all_gates(),
        executor=executor,
    )

    assert result.status == BriefScannerRuntimeStatus.DISPATCHED
    assert result.dispatched is True
    assert result.side_effects_permitted is True
    assert result.dispatched_actions == envelope.approved_actions
    assert result.executor_result == {"batch_id": "synthetic"}
    assert len(batches) == 1
    batch = batches[0]
    assert batch.requires_atomic_execution is True
    assert batch.allows_implicit_actions is False
    assert tuple(item.action for item in batch.invocations) == envelope.approved_actions
    assert len({item.idempotency_key for item in batch.invocations}) == 3
    assert all(len(item.idempotency_key) == 64 for item in batch.invocations)
    assert all(item.authorized and not item.executed for item in batch.invocations)


def test_all_declined_actions_are_a_noop_even_when_gates_are_enabled() -> None:
    envelope, fingerprint = _envelope(decline_all=True)

    result = dispatch_brief_scanner_runtime(
        envelope,
        authorization_fingerprint=fingerprint,
        gates=_all_gates(),
        executor=lambda _batch: pytest.fail("a declined plan must not dispatch"),
    )

    assert result.status == BriefScannerRuntimeStatus.NOOP
    assert result.dispatched_actions == ()
    assert result.blocked_actions == ()


def test_fingerprint_or_command_tampering_fails_before_executor() -> None:
    envelope, fingerprint = _envelope()
    calls = []

    with pytest.raises(ValueError, match="envelope_invalid"):
        dispatch_brief_scanner_runtime(
            envelope,
            authorization_fingerprint="0" * 64,
            gates=_all_gates(),
            executor=lambda batch: calls.append(batch),
        )

    assert envelope.mission is not None
    forged = replace(
        envelope,
        mission=replace(envelope.mission, authorized=False),
    )
    with pytest.raises(ValueError, match="command_invalid"):
        dispatch_brief_scanner_runtime(
            forged,
            authorization_fingerprint=fingerprint,
            gates=_all_gates(),
            executor=lambda batch: calls.append(batch),
        )

    assert calls == []


def test_missing_executor_or_invalid_gate_types_fail_closed() -> None:
    envelope, fingerprint = _envelope()

    with pytest.raises(ValueError, match="executor_missing"):
        dispatch_brief_scanner_runtime(
            envelope,
            authorization_fingerprint=fingerprint,
            gates=_all_gates(),
        )
    with pytest.raises(ValueError, match="gates_invalid"):
        dispatch_brief_scanner_runtime(
            envelope,
            authorization_fingerprint=fingerprint,
            gates=replace(_all_gates(), runtime_enabled=1),  # type: ignore[arg-type]
            executor=lambda _batch: None,
        )
    with pytest.raises(ValueError, match="gates_invalid"):
        dispatch_brief_scanner_runtime(
            envelope,
            authorization_fingerprint=fingerprint,
            gates=False,  # type: ignore[arg-type]
            executor=lambda _batch: None,
        )


def test_runtime_contracts_are_immutable() -> None:
    envelope, fingerprint = _envelope()
    captured = []
    result = dispatch_brief_scanner_runtime(
        envelope,
        authorization_fingerprint=fingerprint,
        gates=_all_gates(),
        executor=lambda batch: captured.append(batch),
    )

    with pytest.raises(FrozenInstanceError):
        result.status = BriefScannerRuntimeStatus.DISABLED  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        captured[0].allows_implicit_actions = True  # type: ignore[misc]
