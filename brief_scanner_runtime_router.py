"""Pure executor routing for supported Brief Scanner runtime batches.

The router selects between the already-bounded Mission-only executor and the atomic
Mission + Reminder executor. Draft-containing batches remain unsupported because provider
generation cannot currently be rolled back or replayed without regeneration. This module
does not construct executors, touch storage, call providers, or wire itself into the app.
"""
from __future__ import annotations

from collections.abc import Callable

from brief_scanner_consent_workflow import BriefScannerConsentAction
from brief_scanner_runtime_adapter import (
    BriefScannerRuntimeBatch,
    BriefScannerRuntimeInvocation,
)

RuntimeExecutor = Callable[[BriefScannerRuntimeBatch], object]

_MISSION_ROUTE = (BriefScannerConsentAction.CREATE_MISSION,)
_MISSION_REMINDER_ROUTE = (
    BriefScannerConsentAction.CREATE_MISSION,
    BriefScannerConsentAction.CREATE_REMINDER,
)


class BriefScannerRuntimeRouterError(RuntimeError):
    """Fail-closed routing error identified by a non-sensitive code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _valid_digest(value: str) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and value == value.casefold()
        and all(character in "0123456789abcdef" for character in value)
    )


def _require_route(
    batch: BriefScannerRuntimeBatch,
) -> tuple[BriefScannerConsentAction, ...]:
    if (
        type(batch) is not BriefScannerRuntimeBatch
        or not _valid_digest(batch.planning_fingerprint)
        or batch.requires_atomic_execution is not True
        or batch.allows_implicit_actions is not False
        or type(batch.invocations) is not tuple
        or not batch.invocations
    ):
        raise BriefScannerRuntimeRouterError(
            "brief_scanner_runtime_router_batch_invalid"
        )
    if any(
        type(invocation) is not BriefScannerRuntimeInvocation
        or type(invocation.action) is not BriefScannerConsentAction
        or invocation.authorized is not True
        or invocation.executed is not False
        for invocation in batch.invocations
    ):
        raise BriefScannerRuntimeRouterError(
            "brief_scanner_runtime_router_invocation_invalid"
        )
    actions = tuple(invocation.action for invocation in batch.invocations)
    if BriefScannerConsentAction.GENERATE_DRAFT in actions:
        raise BriefScannerRuntimeRouterError(
            "brief_scanner_runtime_router_draft_not_supported"
        )
    if actions not in {_MISSION_ROUTE, _MISSION_REMINDER_ROUTE}:
        raise BriefScannerRuntimeRouterError(
            "brief_scanner_runtime_router_batch_not_supported"
        )
    return actions


class BriefScannerRuntimeExecutorRouter:
    """Dispatch one validated batch shape to exactly one injected executor."""

    def __init__(
        self,
        *,
        mission_executor: RuntimeExecutor,
        mission_reminder_executor: RuntimeExecutor,
    ) -> None:
        if not callable(mission_executor) or not callable(mission_reminder_executor):
            raise BriefScannerRuntimeRouterError(
                "brief_scanner_runtime_router_executor_invalid"
            )
        self._mission_executor = mission_executor
        self._mission_reminder_executor = mission_reminder_executor

    def __call__(self, batch: BriefScannerRuntimeBatch) -> object:
        actions = _require_route(batch)
        if actions == _MISSION_ROUTE:
            return self._mission_executor(batch)
        return self._mission_reminder_executor(batch)
