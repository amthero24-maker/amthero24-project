"""Tests for process lifecycle readiness and drain behavior."""
from __future__ import annotations

import asyncio

import pytest

import deployment_lifecycle as lifecycle_module
from deployment_lifecycle import ProcessLifecycle, drain_timeout_seconds


def test_lifecycle_starts_closed_and_transitions_without_personal_data() -> None:
    lifecycle = ProcessLifecycle()
    starting = lifecycle.snapshot()
    assert starting.state == "starting"
    assert starting.accepting_work is False
    assert starting.active_work == 0

    lifecycle.start_accepting()
    assert lifecycle.work_started() is True
    active = lifecycle.snapshot()
    assert active.state == "accepting"
    assert active.active_work == 1
    assert set(active.__dict__) == {"state", "accepting_work", "active_work", "changed_at"}

    lifecycle.begin_drain()
    assert lifecycle.work_started() is False
    assert lifecycle.snapshot().state == "draining"
    lifecycle.work_finished()
    assert lifecycle.snapshot().active_work == 0
    lifecycle.mark_stopped()
    assert lifecycle.snapshot().state == "stopped"


@pytest.mark.anyio
async def test_wait_for_idle_completes_after_inflight_work_finishes() -> None:
    lifecycle = ProcessLifecycle()
    lifecycle.start_accepting()
    assert lifecycle.work_started()

    async def finish() -> None:
        await asyncio.sleep(0)
        lifecycle.work_finished()

    task = asyncio.create_task(finish())
    assert await lifecycle.wait_for_idle(timeout=1) is True
    await task


@pytest.mark.anyio
async def test_wait_for_idle_is_bounded() -> None:
    lifecycle = ProcessLifecycle()
    lifecycle.start_accepting()
    assert lifecycle.work_started()
    assert await lifecycle.wait_for_idle(timeout=0.001) is False
    lifecycle.work_finished()


def test_timeout_is_capped_below_railway_drain_window(monkeypatch) -> None:
    monkeypatch.setenv("GRACEFUL_DRAIN_TIMEOUT_SECONDS", "999")
    assert drain_timeout_seconds() == 12
    monkeypatch.setenv("GRACEFUL_DRAIN_TIMEOUT_SECONDS", "invalid")
    assert drain_timeout_seconds() == 12
    monkeypatch.setenv("GRACEFUL_DRAIN_TIMEOUT_SECONDS", "0")
    assert drain_timeout_seconds() == 1


def test_remaining_budget_is_shared_from_first_drain_transition(monkeypatch) -> None:
    moments = iter((100.0, 103.5, 109.0))
    monkeypatch.setattr(lifecycle_module.time, "monotonic", lambda: next(moments))
    lifecycle = ProcessLifecycle()
    lifecycle.start_accepting()

    lifecycle.begin_drain()
    assert lifecycle.remaining_drain_seconds() == pytest.approx(8.5)
    lifecycle.begin_drain()
    assert lifecycle.remaining_drain_seconds() == pytest.approx(3.0)


@pytest.mark.anyio
async def test_large_worker_timeout_cannot_extend_expired_shared_budget(monkeypatch) -> None:
    lifecycle = ProcessLifecycle()
    lifecycle.start_accepting()
    assert lifecycle.work_started()
    lifecycle.begin_drain()
    monkeypatch.setattr(lifecycle, "remaining_drain_seconds", lambda: 0.001)

    assert await lifecycle.wait_for_idle(timeout=60) is False
    lifecycle.work_finished()
