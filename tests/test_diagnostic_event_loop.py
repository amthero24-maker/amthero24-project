"""Regression tests for non-blocking production diagnostic endpoints."""
from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable
from unittest.mock import patch

import pytest
from starlette.requests import Request

import admin_extensions
import launch_extensions
import runtime_health

ADMIN_TOKEN = "admin-token-2026-unique-8xK2mP7qR4vN"


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"x-admin-token", ADMIN_TOKEN.encode("ascii"))],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 443),
        }
    )


async def _prove_event_loop_progress(
    endpoint: Callable[[], Awaitable[object]],
    *,
    started: threading.Event,
    release: threading.Event,
) -> object:
    progressed = threading.Event()

    async def marker() -> None:
        await asyncio.sleep(0.01)
        progressed.set()

    def controller() -> bool:
        assert started.wait(timeout=1)
        time.sleep(0.05)
        observed = progressed.is_set()
        release.set()
        return observed

    controller_task = asyncio.create_task(asyncio.to_thread(controller))
    marker_task = asyncio.create_task(marker())
    endpoint_task = asyncio.create_task(endpoint())

    observed = await controller_task
    result = await endpoint_task
    await marker_task

    assert observed is True
    return result


@pytest.mark.anyio
async def test_ready_offloads_database_readiness_from_event_loop() -> None:
    started = threading.Event()
    release = threading.Event()

    def slow_readiness(*args, **kwargs):
        started.set()
        assert release.wait(timeout=1)
        return {"status": "ready"}, 200

    with patch.object(runtime_health, "readiness_payload", side_effect=slow_readiness):
        response = await _prove_event_loop_progress(
            runtime_health.ready,
            started=started,
            release=release,
        )

    assert response.status_code == 200


@pytest.mark.anyio
async def test_admin_overview_offloads_aggregate_queries_from_event_loop() -> None:
    started = threading.Event()
    release = threading.Event()

    def slow_overview():
        started.set()
        assert release.wait(timeout=1)
        return {"status": "ok"}

    with patch.dict("os.environ", {"ADMIN_API_TOKEN": ADMIN_TOKEN}, clear=True), patch.object(
        admin_extensions,
        "build_operator_overview",
        side_effect=slow_overview,
    ):
        response = await _prove_event_loop_progress(
            lambda: admin_extensions.admin_overview(_request("/admin/overview")),
            started=started,
            release=release,
        )

    assert response.status_code == 200


@pytest.mark.anyio
async def test_launch_readiness_offloads_aggregate_queries_from_event_loop() -> None:
    started = threading.Event()
    release = threading.Event()

    def slow_inputs():
        started.set()
        assert release.wait(timeout=1)
        return {"storage_backend": "postgresql"}, {}

    report = {
        "status": "ready",
        "summary": {"ready": 0, "warning": 0, "blocked": 0},
        "checks": [],
        "next_actions": [],
        "launch_scope": "controlled_beta",
    }

    with patch.dict("os.environ", {"ADMIN_API_TOKEN": ADMIN_TOKEN}, clear=True), patch.object(
        launch_extensions,
        "_build_launch_inputs",
        side_effect=slow_inputs,
    ), patch.object(
        launch_extensions,
        "build_launch_report",
        return_value=report,
    ), patch.object(
        launch_extensions,
        "apply_closed_beta_launch_check",
        side_effect=lambda payload, metrics: payload,
    ):
        response = await _prove_event_loop_progress(
            lambda: launch_extensions.launch_readiness(
                _request("/admin/launch-readiness")
            ),
            started=started,
            release=release,
        )

    assert response.status_code == 200
