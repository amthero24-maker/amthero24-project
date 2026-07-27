from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time

import pytest

from brief_scanner_consent_workflow import BriefScannerConsentAction
from brief_scanner_draft_planner import BriefScannerDraftKind
from brief_scanner_execution_boundary import (
    BriefScannerDraftCommand,
    BriefScannerExecutionCommandKind,
    BriefScannerMissionCommand,
    BriefScannerReminderCommand,
)
from brief_scanner_mission_planner import BriefScannerMissionKind
from brief_scanner_reminder_planner import BriefScannerReminderKind
from brief_scanner_runtime_adapter import (
    BriefScannerRuntimeBatch,
    BriefScannerRuntimeInvocation,
    brief_scanner_runtime_idempotency_key,
)
from brief_scanner_runtime_router import (
    BriefScannerRuntimeExecutorRouter,
    BriefScannerRuntimeRouterError,
)

_FINGERPRINT = "c" * 64


class _Recorder:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[BriefScannerRuntimeBatch] = []

    def __call__(self, batch: BriefScannerRuntimeBatch) -> object:
        self.calls.append(batch)
        return self.result


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


def _batch(
    actions: tuple[BriefScannerConsentAction, ...],
) -> BriefScannerRuntimeBatch:
    commands = {
        BriefScannerConsentAction.CREATE_MISSION: _mission(),
        BriefScannerConsentAction.GENERATE_DRAFT: _draft(),
        BriefScannerConsentAction.CREATE_REMINDER: _reminder(),
    }
    return BriefScannerRuntimeBatch(
        planning_fingerprint=_FINGERPRINT,
        invocations=tuple(
            BriefScannerRuntimeInvocation(
                action=action,
                idempotency_key=brief_scanner_runtime_idempotency_key(
                    _FINGERPRINT,
                    action,
                ),
                command=commands[action],
            )
            for action in actions
        ),
    )


def _router() -> tuple[
    BriefScannerRuntimeExecutorRouter,
    _Recorder,
    _Recorder,
]:
    mission = _Recorder("mission-result")
    mission_reminder = _Recorder("mission-reminder-result")
    return (
        BriefScannerRuntimeExecutorRouter(
            mission_executor=mission,
            mission_reminder_executor=mission_reminder,
        ),
        mission,
        mission_reminder,
    )


def test_mission_only_batch_routes_to_exactly_one_executor() -> None:
    router, mission, mission_reminder = _router()
    batch = _batch((BriefScannerConsentAction.CREATE_MISSION,))

    result = router(batch)

    assert result == "mission-result"
    assert mission.calls == [batch]
    assert mission_reminder.calls == []


def test_mission_reminder_batch_routes_to_exactly_one_executor() -> None:
    router, mission, mission_reminder = _router()
    batch = _batch(
        (
            BriefScannerConsentAction.CREATE_MISSION,
            BriefScannerConsentAction.CREATE_REMINDER,
        )
    )

    result = router(batch)

    assert result == "mission-reminder-result"
    assert mission.calls == []
    assert mission_reminder.calls == [batch]


def test_executor_failure_never_falls_through_to_another_route() -> None:
    mission_calls: list[BriefScannerRuntimeBatch] = []
    mission_reminder = _Recorder("must-not-run")

    def fail(batch: BriefScannerRuntimeBatch) -> object:
        mission_calls.append(batch)
        raise RuntimeError("synthetic-executor-failure")

    router = BriefScannerRuntimeExecutorRouter(
        mission_executor=fail,
        mission_reminder_executor=mission_reminder,
    )
    batch = _batch((BriefScannerConsentAction.CREATE_MISSION,))

    with pytest.raises(RuntimeError, match="synthetic-executor-failure"):
        router(batch)

    assert mission_calls == [batch]
    assert mission_reminder.calls == []


@pytest.mark.parametrize(
    "actions",
    [
        (
            BriefScannerConsentAction.CREATE_MISSION,
            BriefScannerConsentAction.GENERATE_DRAFT,
        ),
        (
            BriefScannerConsentAction.CREATE_MISSION,
            BriefScannerConsentAction.GENERATE_DRAFT,
            BriefScannerConsentAction.CREATE_REMINDER,
        ),
    ],
)
def test_draft_batches_fail_before_any_executor_call(actions) -> None:
    router, mission, mission_reminder = _router()

    with pytest.raises(
        BriefScannerRuntimeRouterError,
        match="draft_not_supported",
    ):
        router(_batch(actions))

    assert mission.calls == []
    assert mission_reminder.calls == []


def test_reordered_or_replayed_invocations_fail_before_dispatch() -> None:
    router, mission, mission_reminder = _router()
    reordered = _batch(
        (
            BriefScannerConsentAction.CREATE_REMINDER,
            BriefScannerConsentAction.CREATE_MISSION,
        )
    )
    replayed = _batch((BriefScannerConsentAction.CREATE_MISSION,))
    replayed = replace(
        replayed,
        invocations=(replace(replayed.invocations[0], executed=True),),
    )

    with pytest.raises(
        BriefScannerRuntimeRouterError,
        match="batch_not_supported",
    ):
        router(reordered)
    with pytest.raises(
        BriefScannerRuntimeRouterError,
        match="invocation_invalid",
    ):
        router(replayed)

    assert mission.calls == []
    assert mission_reminder.calls == []


def test_invalid_router_configuration_and_batch_fail_closed() -> None:
    with pytest.raises(
        BriefScannerRuntimeRouterError,
        match="executor_invalid",
    ):
        BriefScannerRuntimeExecutorRouter(
            mission_executor=None,  # type: ignore[arg-type]
            mission_reminder_executor=lambda batch: batch,
        )

    router, mission, mission_reminder = _router()
    invalid = replace(
        _batch((BriefScannerConsentAction.CREATE_MISSION,)),
        allows_implicit_actions=True,
    )
    with pytest.raises(
        BriefScannerRuntimeRouterError,
        match="batch_invalid",
    ):
        router(invalid)
    assert mission.calls == []
    assert mission_reminder.calls == []
