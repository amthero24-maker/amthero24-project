"""Regression tests for one process-wide graceful shutdown budget."""
from __future__ import annotations

import asyncio

import pytest

import durable_queue_extensions as durable_module
import privacy_extensions as privacy_module
import reminder_extensions as reminder_module
import runtime_health
import shared_drain_extensions as layer
from deployment_lifecycle import lifecycle


@pytest.fixture(autouse=True)
def restore_worker_globals():
    durable_task = durable_module._WORKER_TASK
    reminder_task = reminder_module._WORKER_TASK
    reminder_stop = reminder_module._WORKER_STOP
    privacy_task = privacy_module._RETENTION_TASK
    privacy_stop = privacy_module._RETENTION_STOP
    lifecycle.start_accepting()
    yield
    durable_module._WORKER_TASK = durable_task
    reminder_module._WORKER_TASK = reminder_task
    reminder_module._WORKER_STOP = reminder_stop
    privacy_module._RETENTION_TASK = privacy_task
    privacy_module._RETENTION_STOP = privacy_stop
    while lifecycle.snapshot().active_work:
        lifecycle.work_finished()
    lifecycle.start_accepting()


def test_runtime_shutdown_order_starts_drain_then_prioritizes_workers() -> None:
    handlers = runtime_health.app.router.on_shutdown

    assert handlers[0] is runtime_health._begin_drain_before_workers
    assert handlers[1:4] == [
        layer._stop_durable_worker,
        layer._stop_reminder_worker,
        layer._stop_privacy_worker,
    ]
    assert handlers[-1] is layer._mark_process_stopped
    assert durable_module._stop_worker not in handlers
    assert reminder_module._stop_worker not in handlers
    assert privacy_module._stop_retention_worker not in handlers


@pytest.mark.anyio
async def test_durable_worker_gets_grace_before_cancel_and_releases_owned_leases(monkeypatch) -> None:
    lifecycle.start_accepting()
    assert lifecycle.work_started()
    lifecycle.begin_drain()
    worker_started = asyncio.Event()
    worker_cancelled = asyncio.Event()

    async def worker() -> None:
        worker_started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            worker_cancelled.set()
            raise

    task = asyncio.create_task(worker())
    await worker_started.wait()
    durable_module._WORKER_TASK = task

    class FakeOwnedRepository:
        def __init__(self) -> None:
            self.released = 0

        def release_owned(self) -> int:
            self.released += 1
            return 1

    repository = FakeOwnedRepository()
    monkeypatch.setattr(durable_module, "OwnedDurableQueueRepository", FakeOwnedRepository)
    monkeypatch.setattr(durable_module, "_repository", lambda: repository)

    stopping = asyncio.create_task(layer._stop_durable_worker())
    await asyncio.sleep(0.01)
    assert worker_cancelled.is_set() is False
    assert stopping.done() is False

    lifecycle.work_finished()
    await stopping

    assert worker_cancelled.is_set() is True
    assert repository.released == 1
    assert durable_module._WORKER_TASK is None


@pytest.mark.anyio
async def test_reminder_worker_uses_no_new_timeout_after_budget_expires(monkeypatch) -> None:
    lifecycle.start_accepting()
    lifecycle.begin_drain()
    monkeypatch.setattr(lifecycle, "remaining_drain_seconds", lambda: 0.0)
    stop = asyncio.Event()
    cancelled = asyncio.Event()

    async def worker() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(worker())
    reminder_module._WORKER_STOP = stop
    reminder_module._WORKER_TASK = task

    class FakeReminderRepository:
        def __init__(self) -> None:
            self.released = 0

        def release_owned(self) -> int:
            self.released += 1
            return 1

    repository = FakeReminderRepository()
    monkeypatch.setattr(reminder_module, "ResilientReminderRepository", FakeReminderRepository)
    monkeypatch.setattr(reminder_module, "_repository", lambda: repository)

    await layer._stop_reminder_worker()

    assert stop.is_set()
    assert cancelled.is_set()
    assert repository.released == 1
    assert reminder_module._WORKER_TASK is None
    assert reminder_module._WORKER_STOP is None


@pytest.mark.anyio
async def test_privacy_worker_uses_remaining_shared_budget_and_clears_globals(monkeypatch) -> None:
    lifecycle.start_accepting()
    lifecycle.begin_drain()
    monkeypatch.setattr(lifecycle, "remaining_drain_seconds", lambda: 0.001)
    stop = asyncio.Event()
    cancelled = asyncio.Event()

    async def worker() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    privacy_module._RETENTION_STOP = stop
    privacy_module._RETENTION_TASK = asyncio.create_task(worker())

    await layer._stop_privacy_worker()

    assert stop.is_set()
    assert cancelled.is_set()
    assert privacy_module._RETENTION_TASK is None
    assert privacy_module._RETENTION_STOP is None


@pytest.mark.anyio
async def test_final_shutdown_handler_marks_process_stopped() -> None:
    lifecycle.start_accepting()
    lifecycle.begin_drain()

    await layer._mark_process_stopped()

    snapshot = lifecycle.snapshot()
    assert snapshot.state == "stopped"
    assert snapshot.accepting_work is False
