"""Final production composition for bounded graceful startup and shutdown.

Startup resets the process-local accepting state. Shutdown marks the app draining before
any worker-specific shutdown handler runs, so `/ready` fails and POST `/webhook` becomes
retryable immediately. A final bounded wait uses only the remaining shared drain budget.
"""
from __future__ import annotations

import logging

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


# This layer is imported last by runtime_health. Insert the phase transition before all
# previously registered worker shutdown callbacks and append the final aggregate wait.
core.app.router.on_startup.insert(0, _start_accepting)
core.app.router.on_shutdown.insert(0, _begin_drain)
core.app.router.on_shutdown.append(_finish_drain)

app = composed.app
store = composed.store
