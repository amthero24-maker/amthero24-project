"""Process-local lifecycle coordination for safe Railway deployment handoff.

The state contains no request, user, message, or credential data. It exists only to make
readiness and background workers agree when a process must stop accepting new work.
"""
from __future__ import annotations

import asyncio
import os
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
        self._idle = asyncio.Event()
        self._idle.set()

    def start_accepting(self) -> None:
        if self._state in {"draining", "stopped"}:
            return
        self._state = "accepting"
        self._accepting_work = True
        self._changed_at = datetime.now(UTC)

    def begin_drain(self) -> None:
        if self._state == "stopped":
            return
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

    async def wait_for_idle(self, timeout: float | None = None) -> bool:
        seconds = drain_timeout_seconds() if timeout is None else max(0.0, float(timeout))
        if self._active_work == 0:
            return True
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
    return min(max(value, 1), 25)


lifecycle = ProcessLifecycle()
