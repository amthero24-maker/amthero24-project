from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from brief_scanner_consent_workflow import BriefScannerConsentAction
from brief_scanner_draft_planner import BriefScannerDraftKind
from brief_scanner_execution_boundary import (
    BriefScannerDraftCommand,
    BriefScannerExecutionCommandKind,
    BriefScannerExecutionEnvelope,
    BriefScannerMissionCommand,
    BriefScannerReminderCommand,
)
from brief_scanner_mission_planner import BriefScannerMissionKind
from brief_scanner_reminder_planner import BriefScannerReminderKind
from brief_scanner_runtime_adapter import (
    BriefScannerRuntimeGates,
    BriefScannerRuntimeStatus,
)
from brief_scanner_runtime_composition import (
    dispatch_supported_brief_scanner_runtime,
)
from brief_scanner_runtime_executor import BriefScannerMissionExecutionStatus
from brief_scanner_runtime_reminder_executor import (
    BriefScannerMissionReminderStatus,
)
from brief_scanner_runtime_router import BriefScannerRuntimeRouterError
from data_store import JsonDataStore

_FINGERPRINT = "d" * 64
_GATE_NAMES = (
    "BRIEF_SCANNER_RUNTIME_ENABLED",
    "BRIEF_SCANNER_RUNTIME_MISSION_ENABLED",
    "BRIEF_SCANNER_RUNTIME_DRAFT_ENABLED",
    "BRIEF_SCANNER_RUNTIME_REMINDER_ENABLED",
)


class _ForbiddenStore:
    @property
    def backend_name(self):
        raise AssertionError("storage must remain untouched")


def _mission() -> BriefScannerMissionCommand:
    return BriefScannerMissionCommand(
        kind=BriefScannerExecutionCommandKind.CREATE_MISSION,
        mission_kind=BriefScannerMissionKind.TRACK_DEADLINE,
        title="Track document deadline",
        topic="document",
        next_step="Complete the required action before the deadline",
        due_date=date(2026, 9, 1),
    )


def _draft() -> BriefScannerDraftCommand:
    return BriefScannerDraftCommand(
        kind=BriefScannerExecutionCommandKind.GENERATE_DRAFT,
        draft_kind=BriefScannerDraftKind.FORMAL_RESPONSE,
        recipient_organization="Synthetic Authority",
        response_instruction="Ask for a two-week extension.",
        document_requested_action="Send the requested documents.",
        source_language="en",
        output_language="de",
        due_date=date(2026, 9, 1),
        reference_number="SYNTHETIC-REF-001",
        contact_channel_hint="email",
    )


def _reminder() -> BriefScannerReminderCommand:
    return BriefScannerReminderCommand(
        kind=BriefScannerExecutionCommandKind.CREATE_REMINDER,
        reminder_kind=BriefScannerReminderKind.DEADLINE,
        title="Synthetic Authority",
        target_date=date(2026, 9, 1),
        lead_days=3,
        scheduled_at_utc=datetime(2026, 8, 29, 7, tzinfo=UTC),
        timezone_name="Europe/Berlin",
        local_delivery_time=time(9),
        source_language="de",
        reference_number="SYNTHETIC-REF-001",
    )


def _envelope(
    actions: tuple[BriefScannerConsentAction, ...],
) -> BriefScannerExecutionEnvelope:
    action_set = frozenset(actions)
    return BriefScannerExecutionEnvelope(
        mission=(
            _mission()
            if BriefScannerConsentAction.CREATE_MISSION in action_set
            else None
        ),
        draft=(
            _draft()
            if BriefScannerConsentAction.GENERATE_DRAFT in action_set
            else None
        ),
        reminder=(
            _reminder()
            if BriefScannerConsentAction.CREATE_REMINDER in action_set
            else None
        ),
        approved_actions=actions,
        declined_actions=tuple(
            action
            for action in (
                BriefScannerConsentAction.CREATE_MISSION,
                BriefScannerConsentAction.GENERATE_DRAFT,
                BriefScannerConsentAction.CREATE_REMINDER,
            )
            if action not in action_set
        ),
        planning_fingerprint=_FINGERPRINT,
        requires_executor=True,
    )


def test_default_disabled_runtime_never_touches_storage(monkeypatch) -> None:
    for name in _GATE_NAMES:
        monkeypatch.delenv(name, raising=False)

    result = dispatch_supported_brief_scanner_runtime(
        _envelope((BriefScannerConsentAction.CREATE_MISSION,)),
        authorization_fingerprint=_FINGERPRINT,
        store=_ForbiddenStore(),
        phone="+491111",
    )

    assert result.status is BriefScannerRuntimeStatus.DISABLED
    assert result.blocked_actions == (BriefScannerConsentAction.CREATE_MISSION,)


def test_missing_action_gate_blocks_before_executor_construction() -> None:
    result = dispatch_supported_brief_scanner_runtime(
        _envelope(
            (
                BriefScannerConsentAction.CREATE_MISSION,
                BriefScannerConsentAction.CREATE_REMINDER,
            )
        ),
        authorization_fingerprint=_FINGERPRINT,
        store=_ForbiddenStore(),
        phone="+491111",
        gates=BriefScannerRuntimeGates(
            runtime_enabled=True,
            mission_enabled=True,
            reminder_enabled=False,
        ),
    )

    assert result.status is BriefScannerRuntimeStatus.BLOCKED_BY_ACTION_GATE
    assert result.blocked_actions == (
        BriefScannerConsentAction.CREATE_REMINDER,
    )


def test_mission_only_execution_is_idempotent_when_explicitly_enabled(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = JsonDataStore(tmp_path / "runtime.json")
    envelope = _envelope((BriefScannerConsentAction.CREATE_MISSION,))
    gates = BriefScannerRuntimeGates(
        runtime_enabled=True,
        mission_enabled=True,
    )

    first = dispatch_supported_brief_scanner_runtime(
        envelope,
        authorization_fingerprint=_FINGERPRINT,
        store=store,
        phone="+491111",
        gates=gates,
    )
    second = dispatch_supported_brief_scanner_runtime(
        envelope,
        authorization_fingerprint=_FINGERPRINT,
        store=store,
        phone="+491111",
        gates=gates,
    )

    assert first.status is BriefScannerRuntimeStatus.DISPATCHED
    assert first.executor_result.status is BriefScannerMissionExecutionStatus.CREATED
    assert second.executor_result.status is BriefScannerMissionExecutionStatus.REPLAYED
    assert len(store.snapshot()["cases"]) == 1
    assert store.snapshot().get("reminders", {}) == {}


def test_mission_reminder_execution_is_atomic_when_explicitly_enabled(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REMINDER_ENCRYPTION_KEY", "synthetic-reminder-key")
    store = JsonDataStore(tmp_path / "runtime.json")
    envelope = _envelope(
        (
            BriefScannerConsentAction.CREATE_MISSION,
            BriefScannerConsentAction.CREATE_REMINDER,
        )
    )
    gates = BriefScannerRuntimeGates(
        runtime_enabled=True,
        mission_enabled=True,
        reminder_enabled=True,
    )

    first = dispatch_supported_brief_scanner_runtime(
        envelope,
        authorization_fingerprint=_FINGERPRINT,
        store=store,
        phone="+491111",
        gates=gates,
    )
    second = dispatch_supported_brief_scanner_runtime(
        envelope,
        authorization_fingerprint=_FINGERPRINT,
        store=store,
        phone="+491111",
        gates=gates,
    )

    assert first.status is BriefScannerRuntimeStatus.DISPATCHED
    assert (
        first.executor_result.status
        is BriefScannerMissionReminderStatus.CREATED
    )
    assert (
        second.executor_result.status
        is BriefScannerMissionReminderStatus.REPLAYED
    )
    assert len(store.snapshot()["cases"]) == 1
    assert len(store.snapshot()["reminders"]) == 1
    assert "+491111" not in (tmp_path / "runtime.json").read_text(encoding="utf-8")


def test_draft_batch_reaches_no_storage_or_provider_even_with_explicit_gates() -> None:
    with pytest.raises(
        BriefScannerRuntimeRouterError,
        match="draft_not_supported",
    ):
        dispatch_supported_brief_scanner_runtime(
            _envelope(
                (
                    BriefScannerConsentAction.CREATE_MISSION,
                    BriefScannerConsentAction.GENERATE_DRAFT,
                )
            ),
            authorization_fingerprint=_FINGERPRINT,
            store=_ForbiddenStore(),
            phone="+491111",
            gates=BriefScannerRuntimeGates(
                runtime_enabled=True,
                mission_enabled=True,
                draft_enabled=True,
            ),
        )
