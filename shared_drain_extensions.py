"""Final worker-shutdown coordination using one process-wide Railway drain budget.

PR #64 introduced process-owned queue and reminder leases. This layer preserves those
repositories and replaces only their registered shutdown callbacks so workers do not each
consume an independent timeout. No request, user, message, or credential data is tracked.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import durable_queue_extensions as durable_module
import outbound_delivery_extensions as composed
import privacy_extensions as privacy_module
import reminder_extensions as reminder_module
from deployment_lifecycle import lifecycle

logger = logging.getLogger("amthero24.shared_drain")
core = composed.core


async def _cancel_and_await(task: asyncio.Task[Any] | None) -> None:
    if task is None:
        return
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _await_with_remaining_budget(task: asyncio.Task[Any] | None) -> None:
    """Wait only inside the process-wide deadline, otherwise cancel immediately."""
    if task is None:
        return
    remaining = lifecycle.remaining_drain_seconds()
    if remaining <= 0:
        await _cancel_and_await(task)
        return
    try:
        await asyncio.wait_for(task, timeout=remaining)
    except (TimeoutError, asyncio.CancelledError):
        await _cancel_and_await(task)


async def _stop_durable_worker() -> None:
    """Let admitted queue work finish before cancelling the polling task."""
    task = durable_module._WORKER_TASK
    durable_module._WORKER_TASK = None
    await lifecycle.wait_for_idle()
    await _cancel_and_await(task)
    try:
        repository = durable_module._repository()
        if isinstance(repository, durable_module.OwnedDurableQueueRepository):
            repository.release_owned()
    except Exception:
        logger.exception("Unable to release process-owned queue leases during drain")


async def _stop_reminder_worker() -> None:
    """Signal reminder shutdown and use only the remaining shared deadline."""
    stop = reminder_module._WORKER_STOP
    task = reminder_module._WORKER_TASK
    if stop is not None:
        stop.set()
    await _await_with_remaining_budget(task)
    try:
        repository = reminder_module._repository()
        if isinstance(repository, reminder_module.ResilientReminderRepository):
            repository.release_owned()
    finally:
        reminder_module._WORKER_TASK = None
        reminder_module._WORKER_STOP = None


async def _stop_privacy_worker() -> None:
    """Stop retention without extending the process-wide shutdown deadline."""
    stop = privacy_module._RETENTION_STOP
    task = privacy_module._RETENTION_TASK
    if stop is not None:
        stop.set()
    await _await_with_remaining_budget(task)
    privacy_module._RETENTION_TASK = None
    privacy_module._RETENTION_STOP = None


async def _mark_process_stopped() -> None:
    lifecycle.mark_stopped()


def _install_shared_shutdown_order() -> None:
    replaced = {
        durable_module._stop_worker,
        reminder_module._stop_worker,
        privacy_module._stop_retention_worker,
    }
    remaining = [handler for handler in core.app.router.on_shutdown if handler not in replaced]
    core.app.router.on_shutdown[:] = [
        _stop_durable_worker,
        _stop_reminder_worker,
        _stop_privacy_worker,
        *remaining,
        _mark_process_stopped,
    ]


_install_shared_shutdown_order()

app = composed.app
store = composed.store
