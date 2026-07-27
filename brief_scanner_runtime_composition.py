"""Lazy, unwired composition for supported Brief Scanner runtime execution.

The composition joins the authorization-aware adapter, fail-closed router, and the two
supported concrete executors. Executor construction is deferred until the adapter confirms
every required gate. The module is not imported by the application composition root and does
not add any WhatsApp, provider, worker, telemetry, or production wiring.
"""
from __future__ import annotations

from typing import Any

from brief_scanner_execution_boundary import BriefScannerExecutionEnvelope
from brief_scanner_runtime_adapter import (
    BriefScannerRuntimeBatch,
    BriefScannerRuntimeDispatchResult,
    BriefScannerRuntimeGates,
    dispatch_brief_scanner_runtime,
)
from brief_scanner_runtime_executor import BriefScannerMissionRuntimeExecutor
from brief_scanner_runtime_reminder_executor import (
    BriefScannerMissionReminderRuntimeExecutor,
)
from brief_scanner_runtime_router import BriefScannerRuntimeExecutorRouter


def dispatch_supported_brief_scanner_runtime(
    envelope: BriefScannerExecutionEnvelope,
    *,
    authorization_fingerprint: str,
    store: Any,
    phone: str,
    gates: BriefScannerRuntimeGates | None = None,
) -> BriefScannerRuntimeDispatchResult:
    """Dispatch a supported batch without constructing executors before gate approval."""

    def execute_mission(batch: BriefScannerRuntimeBatch) -> object:
        return BriefScannerMissionRuntimeExecutor(
            store,
            phone=phone,
        )(batch)

    def execute_mission_reminder(batch: BriefScannerRuntimeBatch) -> object:
        return BriefScannerMissionReminderRuntimeExecutor(
            store,
            phone=phone,
        )(batch)

    router = BriefScannerRuntimeExecutorRouter(
        mission_executor=execute_mission,
        mission_reminder_executor=execute_mission_reminder,
    )
    return dispatch_brief_scanner_runtime(
        envelope,
        authorization_fingerprint=authorization_fingerprint,
        executor=router,
        gates=gates,
    )
