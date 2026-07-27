"""Disabled-by-default runtime adapter for authorized Brief Scanner commands.

The adapter is deliberately not wired into WhatsApp or the application composition root. It
validates the complete execution envelope, checks every required feature gate before dispatch,
and makes at most one executor call with an atomic batch. Missing or invalid configuration never
permits a partial side effect.
"""
from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from brief_scanner_consent_workflow import BriefScannerConsentAction
from brief_scanner_execution_boundary import (
    BriefScannerDraftCommand,
    BriefScannerExecutionCommandKind,
    BriefScannerExecutionEnvelope,
    BriefScannerMissionCommand,
    BriefScannerReminderCommand,
)


class BriefScannerRuntimeStatus(StrEnum):
    NOOP = "noop"
    DISABLED = "disabled"
    BLOCKED_BY_ACTION_GATE = "blocked_by_action_gate"
    DISPATCHED = "dispatched"


@dataclass(frozen=True)
class BriefScannerRuntimeGates:
    runtime_enabled: bool = False
    mission_enabled: bool = False
    draft_enabled: bool = False
    reminder_enabled: bool = False


BriefScannerRuntimeCommand = (
    BriefScannerMissionCommand
    | BriefScannerDraftCommand
    | BriefScannerReminderCommand
)


@dataclass(frozen=True)
class BriefScannerRuntimeInvocation:
    action: BriefScannerConsentAction
    idempotency_key: str
    command: BriefScannerRuntimeCommand
    authorized: bool = True
    executed: bool = False


@dataclass(frozen=True)
class BriefScannerRuntimeBatch:
    planning_fingerprint: str
    invocations: tuple[BriefScannerRuntimeInvocation, ...]
    requires_atomic_execution: bool = True
    allows_implicit_actions: bool = False


@dataclass(frozen=True)
class BriefScannerRuntimeDispatchResult:
    status: BriefScannerRuntimeStatus
    planning_fingerprint: str
    dispatched_actions: tuple[BriefScannerConsentAction, ...] = ()
    blocked_actions: tuple[BriefScannerConsentAction, ...] = ()
    executor_result: object | None = None

    @property
    def dispatched(self) -> bool:
        return self.status == BriefScannerRuntimeStatus.DISPATCHED

    @property
    def side_effects_permitted(self) -> bool:
        return self.dispatched


RuntimeExecutor = Callable[[BriefScannerRuntimeBatch], object]

_ACTION_ORDER = (
    BriefScannerConsentAction.CREATE_MISSION,
    BriefScannerConsentAction.GENERATE_DRAFT,
    BriefScannerConsentAction.CREATE_REMINDER,
)


def _flag(value: str | None) -> bool:
    return (value or "").strip().casefold() in {"1", "true", "yes", "on"}


def brief_scanner_runtime_gates_from_environment() -> BriefScannerRuntimeGates:
    """Read the four independent runtime gates; every missing or invalid value is false."""
    return BriefScannerRuntimeGates(
        runtime_enabled=_flag(os.getenv("BRIEF_SCANNER_RUNTIME_ENABLED")),
        mission_enabled=_flag(os.getenv("BRIEF_SCANNER_RUNTIME_MISSION_ENABLED")),
        draft_enabled=_flag(os.getenv("BRIEF_SCANNER_RUNTIME_DRAFT_ENABLED")),
        reminder_enabled=_flag(os.getenv("BRIEF_SCANNER_RUNTIME_REMINDER_ENABLED")),
    )


def _require_fingerprint(value: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or value != value.casefold()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("brief_scanner_runtime_fingerprint_invalid")
    return value


def _require_gates(gates: BriefScannerRuntimeGates) -> None:
    if type(gates) is not BriefScannerRuntimeGates or any(
        type(value) is not bool
        for value in (
            gates.runtime_enabled,
            gates.mission_enabled,
            gates.draft_enabled,
            gates.reminder_enabled,
        )
    ):
        raise ValueError("brief_scanner_runtime_gates_invalid")


def _require_command(
    action: BriefScannerConsentAction,
    command: BriefScannerRuntimeCommand,
) -> None:
    expected = {
        BriefScannerConsentAction.CREATE_MISSION: (
            BriefScannerMissionCommand,
            BriefScannerExecutionCommandKind.CREATE_MISSION,
        ),
        BriefScannerConsentAction.GENERATE_DRAFT: (
            BriefScannerDraftCommand,
            BriefScannerExecutionCommandKind.GENERATE_DRAFT,
        ),
        BriefScannerConsentAction.CREATE_REMINDER: (
            BriefScannerReminderCommand,
            BriefScannerExecutionCommandKind.CREATE_REMINDER,
        ),
    }[action]
    if (
        type(command) is not expected[0]
        or command.kind != expected[1]
        or not command.authorized
        or command.executed
    ):
        raise ValueError("brief_scanner_runtime_command_invalid")


def _require_envelope(
    envelope: BriefScannerExecutionEnvelope,
    *,
    authorization_fingerprint: str,
) -> tuple[tuple[BriefScannerConsentAction, BriefScannerRuntimeCommand], ...]:
    expected_fingerprint = _require_fingerprint(authorization_fingerprint)
    if (
        type(envelope) is not BriefScannerExecutionEnvelope
        or envelope.executed
        or envelope.allows_implicit_actions
        or envelope.planning_fingerprint != expected_fingerprint
    ):
        raise ValueError("brief_scanner_runtime_envelope_invalid")

    approved = envelope.approved_actions
    declined = envelope.declined_actions
    if (
        any(type(action) is not BriefScannerConsentAction for action in approved + declined)
        or len(approved) != len(frozenset(approved))
        or len(declined) != len(frozenset(declined))
        or frozenset(approved).intersection(declined)
        or tuple(action for action in _ACTION_ORDER if action in approved) != approved
        or tuple(action for action in _ACTION_ORDER if action in declined) != declined
        or BriefScannerConsentAction.CREATE_MISSION
        not in frozenset(approved + declined)
    ):
        raise ValueError("brief_scanner_runtime_actions_invalid")

    action_commands = tuple(
        (action, command)
        for action, command in (
            (BriefScannerConsentAction.CREATE_MISSION, envelope.mission),
            (BriefScannerConsentAction.GENERATE_DRAFT, envelope.draft),
            (BriefScannerConsentAction.CREATE_REMINDER, envelope.reminder),
        )
        if command is not None
    )
    if tuple(action for action, _ in action_commands) != approved:
        raise ValueError("brief_scanner_runtime_actions_invalid")
    if any(
        action != BriefScannerConsentAction.CREATE_MISSION
        for action, _ in action_commands
    ) and envelope.mission is None:
        raise ValueError("brief_scanner_runtime_dependency_invalid")
    if envelope.requires_executor != bool(action_commands):
        raise ValueError("brief_scanner_runtime_executor_requirement_invalid")
    for action, command in action_commands:
        _require_command(action, command)
    return action_commands


def _action_enabled(
    action: BriefScannerConsentAction,
    gates: BriefScannerRuntimeGates,
) -> bool:
    return {
        BriefScannerConsentAction.CREATE_MISSION: gates.mission_enabled,
        BriefScannerConsentAction.GENERATE_DRAFT: gates.draft_enabled,
        BriefScannerConsentAction.CREATE_REMINDER: gates.reminder_enabled,
    }[action]


def brief_scanner_runtime_idempotency_key(
    planning_fingerprint: str,
    action: BriefScannerConsentAction,
) -> str:
    """Return the stable action key shared by the adapter and concrete executors."""
    _require_fingerprint(planning_fingerprint)
    if type(action) is not BriefScannerConsentAction:
        raise ValueError("brief_scanner_runtime_action_invalid")
    material = f"brief-scanner-runtime-v1:{planning_fingerprint}:{action.value}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def dispatch_brief_scanner_runtime(
    envelope: BriefScannerExecutionEnvelope,
    *,
    authorization_fingerprint: str,
    executor: RuntimeExecutor | None = None,
    gates: BriefScannerRuntimeGates | None = None,
) -> BriefScannerRuntimeDispatchResult:
    """Dispatch one authorized atomic batch only when every required gate is enabled."""
    active_gates = (
        brief_scanner_runtime_gates_from_environment()
        if gates is None
        else gates
    )
    _require_gates(active_gates)
    action_commands = _require_envelope(
        envelope,
        authorization_fingerprint=authorization_fingerprint,
    )
    actions = tuple(action for action, _ in action_commands)
    fingerprint = envelope.planning_fingerprint

    if not actions:
        return BriefScannerRuntimeDispatchResult(
            status=BriefScannerRuntimeStatus.NOOP,
            planning_fingerprint=fingerprint,
        )
    if not active_gates.runtime_enabled:
        return BriefScannerRuntimeDispatchResult(
            status=BriefScannerRuntimeStatus.DISABLED,
            planning_fingerprint=fingerprint,
            blocked_actions=actions,
        )

    blocked = tuple(
        action for action in actions if not _action_enabled(action, active_gates)
    )
    if blocked:
        return BriefScannerRuntimeDispatchResult(
            status=BriefScannerRuntimeStatus.BLOCKED_BY_ACTION_GATE,
            planning_fingerprint=fingerprint,
            blocked_actions=blocked,
        )
    if executor is None:
        raise ValueError("brief_scanner_runtime_executor_missing")

    batch = BriefScannerRuntimeBatch(
        planning_fingerprint=fingerprint,
        invocations=tuple(
            BriefScannerRuntimeInvocation(
                action=action,
                idempotency_key=brief_scanner_runtime_idempotency_key(
                    fingerprint,
                    action,
                ),
                command=command,
            )
            for action, command in action_commands
        ),
    )
    executor_result = executor(batch)
    return BriefScannerRuntimeDispatchResult(
        status=BriefScannerRuntimeStatus.DISPATCHED,
        planning_fingerprint=fingerprint,
        dispatched_actions=actions,
        executor_result=executor_result,
    )
