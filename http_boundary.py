"""Harden AmtHero24's public HTTP boundary without inspecting application payloads.

The middleware generates a non-personal request identifier, applies response security
headers, removes implementation headers, enforces bounded request bodies, and cancels
requests that exceed a bounded execution timeout. It never trusts a client-supplied
request identifier and never logs a path query, body, header value, or remote address.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MAX_BODY_BYTES = 256 * 1024
_DEFAULT_WEBHOOK_MAX_BODY_BYTES = 2 * 1024 * 1024
_DEFAULT_TIMEOUT_SECONDS = 30
_DEFAULT_HSTS_MAX_AGE_SECONDS = 31_536_000

_request_id: ContextVar[str] = ContextVar("amthero24_request_id", default="")


class RequestBodyTooLarge(RuntimeError):
    """Raised internally when a streamed request exceeds its route boundary."""


def current_request_id() -> str:
    """Return the current random request correlation identifier, if any."""
    return _request_id.get()


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(value, maximum))


def default_body_limit() -> int:
    return _int_env("HTTP_DEFAULT_MAX_BODY_BYTES", _DEFAULT_MAX_BODY_BYTES, 1_024, 2 * 1024 * 1024)


def webhook_body_limit() -> int:
    return _int_env("HTTP_WEBHOOK_MAX_BODY_BYTES", _DEFAULT_WEBHOOK_MAX_BODY_BYTES, 1_024, 5 * 1024 * 1024)


def request_timeout_seconds() -> int:
    return _int_env("HTTP_REQUEST_TIMEOUT_SECONDS", _DEFAULT_TIMEOUT_SECONDS, 1, 120)


def hsts_max_age_seconds() -> int:
    return _int_env("HTTP_HSTS_MAX_AGE_SECONDS", _DEFAULT_HSTS_MAX_AGE_SECONDS, 0, 63_072_000)


def _headers(scope: dict[str, Any]) -> list[tuple[bytes, bytes]]:
    return [(bytes(key).lower(), bytes(value)) for key, value in scope.get("headers", [])]


def _header_values(scope: dict[str, Any], name: bytes) -> list[str]:
    return [value.decode("latin-1").strip() for key, value in _headers(scope) if key == name]


def _secure_transport(scope: dict[str, Any]) -> bool:
    if str(scope.get("scheme", "")).casefold() == "https":
        return True
    forwarded = _header_values(scope, b"x-forwarded-proto")
    return any(value.split(",", 1)[0].strip().casefold() == "https" for value in forwarded)


def _content_length(scope: dict[str, Any]) -> tuple[int | None, bool]:
    values = _header_values(scope, b"content-length")
    if not values:
        return None, True
    try:
        parsed = [int(value) for value in values]
    except ValueError:
        return None, False
    if any(value < 0 for value in parsed) or len(set(parsed)) != 1:
        return None, False
    return parsed[0], True


def _security_headers(scope: dict[str, Any], request_id: str) -> list[tuple[bytes, bytes]]:
    headers = [
        (b"cache-control", b"no-store, max-age=0"),
        (b"pragma", b"no-cache"),
        (b"expires", b"0"),
        (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"),
        (b"permissions-policy", b"accelerometer=(), camera=(), geolocation=(), gyroscope=(), microphone=(), payment=(), usb=()"),
        (b"referrer-policy", b"no-referrer"),
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"x-robots-tag", b"noindex, nofollow, noarchive"),
        (b"cross-origin-opener-policy", b"same-origin"),
        (b"cross-origin-resource-policy", b"same-origin"),
        (b"x-request-id", request_id.encode("ascii")),
    ]
    max_age = hsts_max_age_seconds()
    if max_age > 0 and _secure_transport(scope):
        headers.append((b"strict-transport-security", f"max-age={max_age}; includeSubDomains".encode("ascii")))
    return headers


def _harden_response_headers(
    original: list[tuple[bytes, bytes]],
    scope: dict[str, Any],
    request_id: str,
) -> list[tuple[bytes, bytes]]:
    protected = {key for key, _ in _security_headers(scope, request_id)}
    remove = protected | {b"server", b"x-powered-by"}
    kept = [(bytes(key).lower(), bytes(value)) for key, value in original if bytes(key).lower() not in remove]
    return kept + _security_headers(scope, request_id)


async def _json_response(
    send: Callable[[dict[str, Any]], Awaitable[None]],
    status: int,
    payload: dict[str, object],
    *,
    extra_headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]
    if extra_headers:
        headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class _LimitedReceive:
    def __init__(self, receive: Callable[[], Awaitable[dict[str, Any]]], limit: int) -> None:
        self.receive = receive
        self.limit = limit
        self.total = 0

    async def __call__(self) -> dict[str, Any]:
        message = await self.receive()
        if message.get("type") == "http.request":
            self.total += len(bytes(message.get("body") or b""))
            if self.total > self.limit:
                raise RequestBodyTooLarge
        return message


class HttpBoundaryMiddleware:
    """Apply fail-closed request bounds and privacy-safe response metadata."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_id = secrets.token_hex(16)
        token = _request_id.set(request_id)
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["request_id"] = request_id

        path = str(scope.get("path", ""))
        limit = webhook_body_limit() if path == "/webhook" else default_body_limit()
        declared_length, length_valid = _content_length(scope)
        response_started = False

        async def secure_send(message: dict[str, Any]) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
                hardened = dict(message)
                hardened["headers"] = _harden_response_headers(
                    list(message.get("headers") or []), scope, request_id
                )
                await send(hardened)
                return
            await send(message)

        try:
            if not length_valid:
                await _json_response(secure_send, 400, {"status": "invalid_request"})
                return
            if declared_length is not None and declared_length > limit:
                await _json_response(secure_send, 413, {"status": "request_too_large"})
                return

            limited_receive = _LimitedReceive(receive, limit)
            try:
                await asyncio.wait_for(
                    self.app(scope, limited_receive, secure_send),
                    timeout=float(request_timeout_seconds()),
                )
            except RequestBodyTooLarge:
                if not response_started:
                    await _json_response(secure_send, 413, {"status": "request_too_large"})
                else:
                    logger.warning("Request body exceeded limit after response start")
            except TimeoutError:
                if not response_started:
                    await _json_response(
                        secure_send,
                        504,
                        {"status": "request_timeout"},
                        extra_headers=[(b"retry-after", b"5")],
                    )
                else:
                    logger.warning("Request timed out after response start")
            except Exception:
                logger.exception("Unhandled ASGI application error")
                if not response_started:
                    await _json_response(secure_send, 500, {"status": "internal_error"})
        finally:
            _request_id.reset(token)
