"""Tests for process lifecycle readiness and drain behavior."""
from __future__ import annotations

import asyncio

import pytest

from deployment_lifecycle import ProcessLifecycle


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
