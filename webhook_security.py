"""ASGI protection for Meta WhatsApp webhook authenticity."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Awaitable, Callable
from typing import Any

from log_safety import install_logging_safety

# Railway starts `webhook_security:app`. Install log safety before any application or
# provider module can create a record containing request, recipient, or credential data.
install_logging_safety()

from encryption_policy import install_encryption_policy  # noqa: E402
from storage_factory import install_production_storage_policy  # noqa: E402

# Install durable-storage and reversible-encryption policies before application
# composition imports modules and binds their functions.
install_production_storage_policy()
install_encryption_policy()

import runtime_health  # noqa: E402

_MAX_WEBHOOK_BODY_BYTES = 2 * 1024 * 1024


def signature_required() -> bool:
    return os.getenv("WEBHOOK_SIGNATURE_REQUIRED", "false").strip().casefold() in {"1", "true", "yes", "on"}


def verify_meta_signature(body: bytes, signature: str, app_secret: str) -> bool:
    """Validate Meta's sha256 HMAC signature using constant-time comparison."""
    if not body or not app_secret or not signature.startswith("sha256="):
        return False
    supplied = signature.split("=", 1)[1].strip().casefold()
    if len(supplied) != 64:
        return False
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied)


async def _json_response(send: Callable[[dict[str, Any]], Awaitable[None]], status: int, payload: dict[str, object]) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
        ],
    })
    await send({"type": "http.response.body", "body": body})


class MetaWebhookSignatureMiddleware:
    """Verify POST /webhook payloads when a Meta App Secret is configured."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Awaitable[dict[str, Any]]], send: Callable[..., Awaitable[None]]) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST" or scope.get("path") != "/webhook":
            await self.app(scope, receive, send)
            return

        app_secret = os.getenv("META_APP_SECRET", "").strip()
        required = signature_required()
        if not app_secret:
            if required:
                await _json_response(send, 503, {"status": "unavailable"})
                return
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                continue
            chunk = bytes(message.get("body") or b"")
            size += len(chunk)
            if size > _MAX_WEBHOOK_BODY_BYTES:
                await _json_response(send, 413, {"status": "rejected"})
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)

        headers = {
            key.decode("latin-1").casefold(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        signature = headers.get("x-hub-signature-256", "")
        if not verify_meta_signature(body, signature, app_secret):
            await _json_response(send, 403, {"status": "rejected"})
            return

        replayed = False

        async def replay_receive() -> dict[str, Any]:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)


app = MetaWebhookSignatureMiddleware(runtime_health.app)
