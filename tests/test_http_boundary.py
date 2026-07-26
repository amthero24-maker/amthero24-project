"""Tests for AmtHero24's hardened public HTTP boundary."""
from __future__ import annotations

import asyncio
import logging
import re
from io import StringIO
from typing import Any

import http_boundary
from http_boundary import HttpBoundaryMiddleware, current_request_id
from log_safety import install_logging_safety


def _scope(
    path: str = "/health",
    *,
    method: str = "GET",
    scheme: str = "http",
    headers: list[tuple[bytes, bytes]] | None = None,
    scope_type: str = "http",
) -> dict[str, Any]:
    return {
        "type": scope_type,
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": scheme,
        "path": path,
        "raw_path": path.encode("ascii", errors="ignore"),
        "query_string": b"",
        "headers": list(headers or []),
        "state": {},
    }


async def _invoke(
    app,
    scope: dict[str, Any],
    messages: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    queue = list(messages or [{"type": "http.request", "body": b"", "more_body": False}])
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        if queue:
            return queue.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    return sent


def _start(messages: list[dict[str, Any]]) -> dict[str, Any]:
    return next(message for message in messages if message.get("type") == "http.response.start")


def _body(messages: list[dict[str, Any]]) -> bytes:
    return b"".join(
        bytes(message.get("body") or b"")
        for message in messages
        if message.get("type") == "http.response.body"
    )


def _headers(message: dict[str, Any]) -> dict[bytes, bytes]:
    return {bytes(key).lower(): bytes(value) for key, value in message.get("headers", [])}


def test_success_response_gets_security_headers_and_untrusted_request_id_is_ignored() -> None:
    observed: dict[str, str] = {}

    async def inner(scope, receive, send) -> None:
        observed["context"] = current_request_id()
        observed["state"] = scope["state"]["request_id"]
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"server", b"unsafe-server"),
                    (b"x-powered-by", b"unsafe-framework"),
                    (b"cache-control", b"public"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"{}"})

    scope = _scope(headers=[(b"x-request-id", b"attacker-controlled")])
    messages = asyncio.run(_invoke(HttpBoundaryMiddleware(inner), scope))
    response_headers = _headers(_start(messages))
    request_id = response_headers[b"x-request-id"].decode("ascii")

    assert re.fullmatch(r"[0-9a-f]{32}", request_id)
    assert request_id != "attacker-controlled"
    assert observed == {"context": request_id, "state": request_id}
    assert response_headers[b"cache-control"] == b"no-store, max-age=0"
    assert response_headers[b"pragma"] == b"no-cache"
    assert response_headers[b"x-content-type-options"] == b"nosniff"
    assert response_headers[b"x-frame-options"] == b"DENY"
    assert response_headers[b"referrer-policy"] == b"no-referrer"
    assert response_headers[b"cross-origin-opener-policy"] == b"same-origin"
    assert response_headers[b"cross-origin-resource-policy"] == b"same-origin"
    assert b"server" not in response_headers
    assert b"x-powered-by" not in response_headers
    assert response_headers[b"content-type"] == b"application/json"
    assert current_request_id() == ""


def test_https_or_forwarded_https_adds_hsts_but_plain_http_does_not(monkeypatch) -> None:
    monkeypatch.setenv("HTTP_HSTS_MAX_AGE_SECONDS", "12345")

    async def inner(scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    secure = asyncio.run(_invoke(HttpBoundaryMiddleware(inner), _scope(scheme="https")))
    forwarded = asyncio.run(
        _invoke(
            HttpBoundaryMiddleware(inner),
            _scope(headers=[(b"x-forwarded-proto", b"https")]),
        )
    )
    plain = asyncio.run(_invoke(HttpBoundaryMiddleware(inner), _scope()))

    assert _headers(_start(secure))[b"strict-transport-security"] == b"max-age=12345; includeSubDomains"
    assert b"strict-transport-security" in _headers(_start(forwarded))
    assert b"strict-transport-security" not in _headers(_start(plain))


def test_invalid_or_conflicting_content_length_fails_before_inner_app() -> None:
    calls = 0

    async def inner(scope, receive, send) -> None:
        nonlocal calls
        calls += 1

    invalid = asyncio.run(
        _invoke(
            HttpBoundaryMiddleware(inner),
            _scope(method="POST", headers=[(b"content-length", b"not-a-number")]),
        )
    )
    conflicting = asyncio.run(
        _invoke(
            HttpBoundaryMiddleware(inner),
            _scope(
                method="POST",
                headers=[(b"content-length", b"10"), (b"content-length", b"11")],
            ),
        )
    )

    assert _start(invalid)["status"] == 400
    assert _start(conflicting)["status"] == 400
    assert calls == 0


def test_declared_and_streamed_oversize_requests_return_413(monkeypatch) -> None:
    monkeypatch.setattr(http_boundary, "default_body_limit", lambda: 10)
    calls = 0

    async def inner(scope, receive, send) -> None:
        nonlocal calls
        calls += 1
        while True:
            message = await receive()
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    declared = asyncio.run(
        _invoke(
            HttpBoundaryMiddleware(inner),
            _scope(method="POST", headers=[(b"content-length", b"11")]),
        )
    )
    streamed = asyncio.run(
        _invoke(
            HttpBoundaryMiddleware(inner),
            _scope(method="POST"),
            [
                {"type": "http.request", "body": b"123456", "more_body": True},
                {"type": "http.request", "body": b"789012", "more_body": False},
            ],
        )
    )

    assert _start(declared)["status"] == 413
    assert _start(streamed)["status"] == 413
    assert calls == 1  # streamed input enters the app; declared oversize does not


def test_webhook_uses_its_separate_larger_body_limit(monkeypatch) -> None:
    monkeypatch.setattr(http_boundary, "default_body_limit", lambda: 5)
    monkeypatch.setattr(http_boundary, "webhook_body_limit", lambda: 10)

    async def inner(scope, receive, send) -> None:
        message = await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": bytes(message.get("body") or b"")})

    payload = {"type": "http.request", "body": b"12345678", "more_body": False}
    webhook = asyncio.run(
        _invoke(HttpBoundaryMiddleware(inner), _scope("/webhook", method="POST"), [payload])
    )
    admin = asyncio.run(
        _invoke(HttpBoundaryMiddleware(inner), _scope("/admin/overview", method="POST"), [payload])
    )

    assert _start(webhook)["status"] == 200
    assert _body(webhook) == b"12345678"
    assert _start(admin)["status"] == 413


def test_timeout_and_unhandled_error_return_generic_hardened_responses(monkeypatch) -> None:
    monkeypatch.setattr(http_boundary, "request_timeout_seconds", lambda: 0.01)

    async def slow(scope, receive, send) -> None:
        await asyncio.sleep(0.1)

    async def broken(scope, receive, send) -> None:
        raise RuntimeError("private failure details +4915123456789")

    timed_out = asyncio.run(_invoke(HttpBoundaryMiddleware(slow), _scope()))
    failed = asyncio.run(_invoke(HttpBoundaryMiddleware(broken), _scope()))

    timeout_headers = _headers(_start(timed_out))
    assert _start(timed_out)["status"] == 504
    assert timeout_headers[b"retry-after"] == b"5"
    assert timeout_headers[b"cache-control"] == b"no-store, max-age=0"
    assert b"private" not in _body(timed_out)

    assert _start(failed)["status"] == 500
    assert b"private failure" not in _body(failed)
    assert _headers(_start(failed))[b"x-content-type-options"] == b"nosniff"


def test_request_id_correlates_sanitized_logs_and_is_reset_after_request() -> None:
    install_logging_safety()
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("amthero24.http-boundary-test")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    async def inner(scope, receive, send) -> None:
        logger.info("request handled")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    messages = asyncio.run(_invoke(HttpBoundaryMiddleware(inner), _scope()))
    request_id = _headers(_start(messages))[b"x-request-id"].decode("ascii")

    assert f"[request_id={request_id}]" in stream.getvalue()
    assert current_request_id() == ""


def test_non_http_scope_passes_through_without_http_headers() -> None:
    seen: list[str] = []

    async def inner(scope, receive, send) -> None:
        seen.append(scope["type"])
        await send({"type": "websocket.close", "code": 1000})

    sent = asyncio.run(
        _invoke(
            HttpBoundaryMiddleware(inner),
            _scope(scope_type="websocket"),
            [{"type": "websocket.connect"}],
        )
    )

    assert seen == ["websocket"]
    assert sent == [{"type": "websocket.close", "code": 1000}]
