"""Tests for privacy-safe process lifecycle coordination."""
from __future__ import annotations

import asyncio

import pytest

from runtime_lifecycle import (
    RuntimeLifecycle,
    lifecycle_status,
    shutdown_grace_seconds,
    shutdown_retry_delay_seconds,
)


def test_accepting_work_is_counted_and_draining_blocks_new_work() -> None:
    controller = RuntimeLifecycle()

    assert controller.try_start_work() is True
    assert controller.try_start_work() is True
    assert controller.snapshot().active_work == 2
    assert controller.begin_draining() is True
    assert controller.begin_draining() is False
    assert controller.try_start_work() is False

    controller.finish_work()
    controller.finish_work()
    controller.finish_work()
    assert controller.snapshot().active_work == 0

    controller.reset_accepting()
    assert controller.snapshot().phase == "accepting"
    assert controller.try_start_work() is True


@pytest.mark.anyio
async def test_wait_for_idle_observes_completion() -> None:
    controller = RuntimeLifecycle()
    assert controller.try_start_work()

    async def finish() -> None:
        await asyncio.sleep(0.01)
        controller.finish_work()

    task = asyncio.create_task(finish())
    assert await controller.wait_for_idle(timeout=0.5) is True
    await task


@pytest.mark.anyio
async def test_wait_for_idle_respects_short_requested_timeout() -> None:
    controller = RuntimeLifecycle()
    assert controller.try_start_work()
    controller.begin_draining()

    assert await controller.wait_for_idle(timeout=0.01) is False
    controller.finish_work()


def test_shutdown_settings_are_bounded(monkeypatch) -> None:
    monkeypatch.setenv("SHUTDOWN_GRACE_SECONDS", "999")
    monkeypatch.setenv("SHUTDOWN_RETRY_DELAY_SECONDS", "1")
    assert shutdown_grace_seconds() == 12
    assert shutdown_retry_delay_seconds() == 5

    monkeypatch.setenv("SHUTDOWN_GRACE_SECONDS", "invalid")
    monkeypatch.setenv("SHUTDOWN_RETRY_DELAY_SECONDS", "invalid")
    assert shutdown_grace_seconds() == 10
    assert shutdown_retry_delay_seconds() == 30


def test_public_lifecycle_status_contains_only_aggregate_state(monkeypatch) -> None:
    from runtime_lifecycle import lifecycle

    lifecycle.reset_accepting()
    assert lifecycle.try_start_work()
    payload = lifecycle_status()
    lifecycle.finish_work()

    assert payload == {"phase": "accepting", "active_work": 1}
    encoded = str(payload).casefold()
    assert "phone" not in encoded
    assert "message" not in encoded
    assert "sender" not in encoded
