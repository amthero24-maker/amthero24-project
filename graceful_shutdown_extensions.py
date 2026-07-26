"""Final production composition for bounded graceful startup and shutdown.

Startup resets the process-local accepting state. Shutdown marks the app draining before
any worker-specific shutdown handler runs, so `/ready` fails and POST `/webhook` becomes
retryable immediately. Existing worker callbacks are ordered and bounded by one shared
shutdown deadline instead of each consuming an independent timeout.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import Any

import outbound_delivery_extensions as composed
from runtime_lifecycle import lifecycle

logger = logging.getLogger("amthero24.lifecycle")
core = composed.core


async def _start_accepting() -> None:
    lifecycle.reset_accepting()


async def _begin_drain() -> None:
    if lifecycle.begin_draining():
        logger.info("Runtime entered graceful drain")


async def _finish_drain() -> None:
    idle = await lifecycle.wait_for_idle()
    if idle:
        logger.info("Runtime drain completed")
    else:
        logger.warning("Runtime drain budget expired with active work")


def _priority(handler: Callable[..., Any]) -> tuple[int, str, str]:
    module = str(getattr(handler, "__module__", ""))
    name = str(getattr(handler, "__name__", ""))
    if module.endswith("durable_queue_extensions"):
        rank = 0
    elif module.endswith("reminder_extensions"):
        rank = 1
    elif module.endswith("privacy_extensions"):
        rank = 2
    else:
        rank = 3
    return rank, module, name


def _bounded(handler: Callable[..., Any]) -> Callable[[], Any]:
    module = str(getattr(handler, "__module__", "worker"))
    name = str(getattr(handler, "__name__", "shutdown"))

    async def bounded_shutdown() -> None:
        async def invoke() -> None:
            result = handler()
            if inspect.isawaitable(result):
                await result

        remaining = lifecycle.remaining_grace_seconds()
        task = asyncio.create_task(invoke(), name=f"amthero24-shutdown-{name}")
        try:
            await asyncio.wait_for(task, timeout=max(0.05, remaining))
        except TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.warning("Worker shutdown exceeded shared drain budget", extra={"component": module})
        except asyncio.CancelledError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            raise

    bounded_shutdown.__name__ = f"bounded_{name}"
    bounded_shutdown.__module__ = __name__
    return bounded_shutdown


# This layer is imported last by runtime_health. Preserve every registered callback while
# forcing a safe priority and a single total deadline.
existing_shutdown = list(core.app.router.on_shutdown)
core.app.router.on_startup.insert(0, _start_accepting)
core.app.router.on_shutdown[:] = [
    _begin_drain,
    *[_bounded(handler) for handler in sorted(existing_shutdown, key=_priority)],
    _finish_drain,
]

app = composed.app
store = composed.store
