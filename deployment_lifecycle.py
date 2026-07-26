"""Process-local lifecycle coordination for safe Railway deployment handoff.

The state contains no request, user, message, or credential data. It exists only to make
readiness and background workers agree when a process must stop accepting new work.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class LifecycleSnapshot:
    state: str
    accepting_work: bool
    active_work: int
    changed_at: str


class ProcessLifecycle:
    def __init__(self) -> None:
        self._state = "starting"
        self._accepting_work = False
        self._active_work = 0
        self._changed_at = datetime.now(UTC)
        self._drain_started_monotonic: float | None = None
        self._idle = asyncio.Event()
        self._idle.set()

    def start_accepting(self) -> None:
        # A fresh ASGI lifespan may start again in the same interpreter during tests or
        # controlled embedding. Reopening is safe only after every prior task drained.
        if self._active_work != 0:
            return
        self._state = "accepting"
        self._accepting_work = True
        self._drain_started_monotonic = None
        self._changed_at = datetime.now(UTC)

    def begin_drain(self) -> None:
        if self._state == "stopped":
            return
        if self._drain_started_monotonic is None:
            self._drain_started_monotonic = time.monotonic()
        self._state = "draining"
        self._accepting_work = False
        self._changed_at = datetime.now(UTC)

    def mark_stopped(self) -> None:
        self._state = "stopped"
        self._accepting_work = False
        self._changed_at = datetime.now(UTC)

    def work_started(self) -> bool:
        if not self._accepting_work:
            return False
        self._active_work += 1
        self._idle.clear()
        return True

    def work_finished(self) -> None:
        self._active_work = max(0, self._active_work - 1)
        if self._active_work == 0:
            self._idle.set()

    def remaining_drain_seconds(self) -> float:
        """Return the remaining process-wide drain budget."""
        if self._drain_started_monotonic is None:
            return float(drain_timeout_seconds())
        elapsed = time.monotonic() - self._drain_started_monotonic
        return max(0.0, float(drain_timeout_seconds()) - elapsed)

    async def wait_for_idle(self, timeout: float | None = None) -> bool:
        available = self.remaining_drain_seconds()
        requested = available if timeout is None else max(0.0, float(timeout))
        seconds = min(available, requested)
        if self._active_work == 0:
            return True
        if seconds <= 0:
            return False
        try:
            await asyncio.wait_for(self._idle.wait(), timeout=seconds)
            return True
        except TimeoutError:
            return False

    def snapshot(self) -> LifecycleSnapshot:
        return LifecycleSnapshot(
            state=self._state,
            accepting_work=self._accepting_work,
            active_work=self._active_work,
            changed_at=self._changed_at.isoformat(),
        )


def drain_timeout_seconds() -> int:
    try:
        value = int(os.getenv("GRACEFUL_DRAIN_TIMEOUT_SECONDS", "12").strip())
    except ValueError:
        value = 12
    # Railway's configured drain window is 15 seconds. Keep application work below it so
    # the interpreter and connection pools still have time to close.
    return min(max(value, 1), 12)


lifecycle = ProcessLifecycle()
