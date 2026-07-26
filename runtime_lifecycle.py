"""Process-local lifecycle coordination for graceful Railway shutdown.

The coordinator tracks only an aggregate active-work count. It never receives or stores
message IDs, phone numbers, content, ciphertext, or provider payloads. New webhook work
is rejected after draining starts, while already-running work gets a bounded opportunity
to finish before worker cancellation and durable lease release.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
from dataclasses import dataclass


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError:
        value = default
    return min(max(value, minimum), maximum)


def shutdown_grace_seconds() -> int:
    """Bound shutdown work below Railway's configured drain/overlap window."""
    return _bounded_int("SHUTDOWN_GRACE_SECONDS", 10, 1, 25)


def shutdown_retry_delay_seconds() -> int:
    """Delay forced-release retries to reduce immediate duplicate delivery risk."""
    return _bounded_int("SHUTDOWN_RETRY_DELAY_SECONDS", 30, 5, 300)


@dataclass(frozen=True)
class LifecycleSnapshot:
    phase: str
    active_work: int


class RuntimeLifecycle:
    """Thread-safe accepting/draining state with one shared shutdown deadline."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._phase = "accepting"
        self._active_work = 0
        self._drain_started: float | None = None

    def reset_accepting(self) -> None:
        with self._lock:
            self._phase = "accepting"
            self._active_work = 0
            self._drain_started = None

    def begin_draining(self) -> bool:
        """Enter draining once; return whether this call established the deadline."""
        with self._lock:
            changed = self._phase != "draining"
            self._phase = "draining"
            if self._drain_started is None:
                self._drain_started = time.monotonic()
            return changed

    def try_start_work(self) -> bool:
        """Atomically reserve one active-work slot unless shutdown has begun."""
        with self._lock:
            if self._phase == "draining":
                return False
            self._active_work += 1
            return True

    def finish_work(self) -> None:
        with self._lock:
            self._active_work = max(0, self._active_work - 1)

    def snapshot(self) -> LifecycleSnapshot:
        with self._lock:
            return LifecycleSnapshot(self._phase, self._active_work)

    def is_draining(self) -> bool:
        return self.snapshot().phase == "draining"

    def remaining_grace_seconds(self) -> float:
        """Return the remaining shared drain budget without exposing process timing."""
        with self._lock:
            started = self._drain_started
        if started is None:
            return float(shutdown_grace_seconds())
        return max(0.0, float(shutdown_grace_seconds()) - (time.monotonic() - started))

    async def wait_for_idle(self, timeout: float | None = None) -> bool:
        """Wait for aggregate work using at most the remaining shared drain budget."""
        available = self.remaining_grace_seconds()
        requested = available if timeout is None else max(0.0, min(float(timeout), 30.0))
        bounded = min(available, requested)
        deadline = time.monotonic() + bounded
        while self.snapshot().active_work > 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.05, remaining))
        return True


lifecycle = RuntimeLifecycle()


def lifecycle_status() -> dict[str, int | str]:
    """Return a public-safe aggregate component status."""
    snapshot = lifecycle.snapshot()
    return {"phase": snapshot.phase, "active_work": snapshot.active_work}
