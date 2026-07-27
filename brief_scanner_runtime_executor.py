"""Concrete, disabled-by-default Mission executor for Brief Scanner runtime batches.

Only a single CREATE_MISSION invocation is currently supported. Draft and Reminder batches
fail closed before persistence until their cross-service atomic execution contract exists.
The executor is deliberately not wired into the application composition root.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from brief_scanner_consent_workflow import BriefScannerConsentAction
from brief_scanner_execution_boundary import (
    BriefScannerExecutionCommandKind,
    BriefScannerMissionCommand,
)
from brief_scanner_runtime_adapter import (
    BriefScannerRuntimeBatch,
    BriefScannerRuntimeInvocation,
    brief_scanner_runtime_idempotency_key,
)
from hero_memory import HeroMemory


class BriefScannerRuntimeExecutionError(RuntimeError):
    """Fail-closed executor error identified by a non-sensitive code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class BriefScannerMissionExecutionStatus(StrEnum):
    CREATED = "created"
    REPLAYED = "replayed"


@dataclass(frozen=True)
class BriefScannerMissionExecutionResult:
    status: BriefScannerMissionExecutionStatus
    planning_fingerprint: str
    mission_id: str
    executed_actions: tuple[BriefScannerConsentAction, ...]


def _valid_hex_digest(value: str) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and value == value.casefold()
        and all(character in "0123456789abcdef" for character in value)
    )


def _require_phone(phone: str) -> str:
    if type(phone) is not str:
        raise BriefScannerRuntimeExecutionError("brief_scanner_runtime_tenant_invalid")
    normalized = "".join(
        character for character in phone if character.isdigit() or character == "+"
    )
    digits = normalized.lstrip("+")
    if (
        not digits
        or not digits.isdigit()
        or normalized.count("+") > 1
        or ("+" in normalized and not normalized.startswith("+"))
        or len(normalized) > 32
    ):
        raise BriefScannerRuntimeExecutionError("brief_scanner_runtime_tenant_invalid")
    return normalized


def _require_mission_batch(
    batch: BriefScannerRuntimeBatch,
) -> tuple[BriefScannerRuntimeInvocation, BriefScannerMissionCommand]:
    if (
        type(batch) is not BriefScannerRuntimeBatch
        or not _valid_hex_digest(batch.planning_fingerprint)
        or batch.requires_atomic_execution is not True
        or batch.allows_implicit_actions is not False
    ):
        raise BriefScannerRuntimeExecutionError("brief_scanner_runtime_batch_invalid")
    if len(batch.invocations) != 1:
        raise BriefScannerRuntimeExecutionError(
            "brief_scanner_runtime_batch_not_supported"
        )

    invocation = batch.invocations[0]
    if (
        type(invocation) is not BriefScannerRuntimeInvocation
        or invocation.action is not BriefScannerConsentAction.CREATE_MISSION
        or invocation.authorized is not True
        or invocation.executed is not False
    ):
        raise BriefScannerRuntimeExecutionError(
            "brief_scanner_runtime_invocation_invalid"
        )
    expected_key = brief_scanner_runtime_idempotency_key(
        batch.planning_fingerprint,
        BriefScannerConsentAction.CREATE_MISSION,
    )
    if invocation.idempotency_key != expected_key:
        raise BriefScannerRuntimeExecutionError(
            "brief_scanner_runtime_idempotency_invalid"
        )

    command = invocation.command
    if (
        type(command) is not BriefScannerMissionCommand
        or command.kind is not BriefScannerExecutionCommandKind.CREATE_MISSION
        or command.authorized is not True
        or command.executed is not False
        or command.source != "brief_scanner"
    ):
        raise BriefScannerRuntimeExecutionError(
            "brief_scanner_runtime_command_invalid"
        )
    return invocation, command


class BriefScannerMissionRuntimeExecutor:
    """Persist one authorized Mission atomically and idempotently for one tenant."""

    def __init__(self, store: Any, *, phone: str) -> None:
        self.memory = HeroMemory(store)
        self.phone = _require_phone(phone)

    def __call__(
        self,
        batch: BriefScannerRuntimeBatch,
    ) -> BriefScannerMissionExecutionResult:
        invocation, command = _require_mission_batch(batch)
        mission = self.memory.create_mission(
            self.phone,
            title=command.title,
            topic=command.topic,
            next_step=command.next_step,
            due_at=command.due_date.isoformat() if command.due_date else None,
            metadata={
                "source": command.source,
                "category": command.mission_kind.value,
            },
            idempotency_key=invocation.idempotency_key,
        )
        operation = str(mission.get("_operation") or "")
        if operation == "created":
            status = BriefScannerMissionExecutionStatus.CREATED
        elif operation == "replayed":
            status = BriefScannerMissionExecutionStatus.REPLAYED
        else:
            raise BriefScannerRuntimeExecutionError(
                "brief_scanner_runtime_persistence_invalid"
            )
        mission_id = str(mission.get("mission_id") or "")
        if len(mission_id) != 32:
            raise BriefScannerRuntimeExecutionError(
                "brief_scanner_runtime_persistence_invalid"
            )
        return BriefScannerMissionExecutionResult(
            status=status,
            planning_fingerprint=batch.planning_fingerprint,
            mission_id=mission_id,
            executed_actions=(BriefScannerConsentAction.CREATE_MISSION,),
        )
